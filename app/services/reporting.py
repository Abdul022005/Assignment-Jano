"""
Reporting service.

Two aggregation pipelines, both running entirely in MongoDB — no Python-side
filtering or counting. This is intentional: pushing the work into the DB
uses the compound indexes created in Stage 2 and avoids pulling large
result sets into application memory.

Pipeline 1 — patients_with_unresolved_conflicts:
  "List all patients in Clinic X with >= N unresolved conflicts."
  Uses index: (clinic_id, status, detected_at)

Pipeline 2 — conflict_counts_last_n_days:
  "For the past N days, count patients with >= N conflicts per clinic."
  Uses index: (clinic_id, status, detected_at)

Both pipelines follow the same structure:
  $match  → filter by clinic + status (+ date window for pipeline 2)
  $group  → count conflicts per patient
  $match  → keep only patients meeting the minimum threshold
  $lookup → join patient name from patients collection
  $project → shape the final output
"""

from datetime import datetime, timedelta, timezone

from app.db.collections import conflicts


async def patients_with_unresolved_conflicts(
    clinic_id: str,
    min_conflicts: int = 1,
) -> list[dict]:
    """
    Return patients in a clinic who have >= min_conflicts unresolved conflicts.

    Each result includes patient_id, patient name, clinic_id,
    and the count of unresolved conflicts.
    """
    pipeline = [
        # Step 1: filter to this clinic's unresolved conflicts only
        # Uses compound index (clinic_id, status, detected_at)
        {
            "$match": {
                "clinic_id": clinic_id,
                "status": "unresolved",
            }
        },
        # Step 2: count conflicts per patient
        {
            "$group": {
                "_id": "$patient_id",
                "unresolved_count": {"$sum": 1},
                "conflict_types": {"$addToSet": "$conflict_type"},
                "oldest_conflict": {"$min": "$detected_at"},
            }
        },
        # Step 3: keep only patients meeting the threshold
        {
            "$match": {
                "unresolved_count": {"$gte": min_conflicts}
            }
        },
        # Step 4: join patient name from patients collection
        {
            "$lookup": {
                "from": "patients",
                "localField": "_id",
                "foreignField": "_id",
                "as": "patient_info",
            }
        },
        # Step 5: flatten the patient_info array
        {
            "$unwind": {
                "path": "$patient_info",
                "preserveNullAndEmptyArrays": True,  # keep even if patient missing
            }
        },
        # Step 6: shape final output
        {
            "$project": {
                "_id": 0,
                "patient_id": "$_id",
                "patient_name": {"$ifNull": ["$patient_info.name", "Unknown"]},
                "clinic_id": {"$ifNull": ["$patient_info.clinic_id", clinic_id]},
                "unresolved_conflict_count": "$unresolved_count",
                "conflict_types": 1,
                "oldest_unresolved_conflict": "$oldest_conflict",
            }
        },
        # Step 7: sort by most conflicts first
        {
            "$sort": {"unresolved_conflict_count": -1}
        },
    ]

    results = []
    async for doc in conflicts().aggregate(pipeline):
        results.append(doc)
    return results


async def conflict_counts_last_n_days(
    clinic_id: str,
    days: int = 30,
    min_conflicts: int = 2,
) -> dict:
    """
    For the past `days` days, return patients in clinic_id who had
    >= min_conflicts conflicts (any status) detected in that window.

    Returns a summary with:
      - the query parameters used
      - total matching patients
      - per-patient breakdown
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        # Step 1: filter to this clinic, within the date window
        # Uses compound index (clinic_id, status, detected_at)
        {
            "$match": {
                "clinic_id": clinic_id,
                "detected_at": {"$gte": since},
            }
        },
        # Step 2: count all conflicts per patient in the window
        {
            "$group": {
                "_id": "$patient_id",
                "total_conflicts": {"$sum": 1},
                "unresolved_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "unresolved"]}, 1, 0]
                    }
                },
                "resolved_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]
                    }
                },
                "conflict_types": {"$addToSet": "$conflict_type"},
            }
        },
        # Step 3: keep only patients meeting the minimum threshold
        {
            "$match": {
                "total_conflicts": {"$gte": min_conflicts}
            }
        },
        # Step 4: join patient name
        {
            "$lookup": {
                "from": "patients",
                "localField": "_id",
                "foreignField": "_id",
                "as": "patient_info",
            }
        },
        {
            "$unwind": {
                "path": "$patient_info",
                "preserveNullAndEmptyArrays": True,
            }
        },
        # Step 5: shape output
        {
            "$project": {
                "_id": 0,
                "patient_id": "$_id",
                "patient_name": {"$ifNull": ["$patient_info.name", "Unknown"]},
                "total_conflicts": 1,
                "unresolved_count": 1,
                "resolved_count": 1,
                "conflict_types": 1,
            }
        },
        {
            "$sort": {"total_conflicts": -1}
        },
    ]

    rows = []
    async for doc in conflicts().aggregate(pipeline):
        rows.append(doc)

    return {
        "clinic_id": clinic_id,
        "window_days": days,
        "since": since.isoformat(),
        "min_conflicts": min_conflicts,
        "matching_patient_count": len(rows),
        "patients": rows,
    }