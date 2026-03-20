"""
Clinics router.

Endpoints:
  POST /clinics                                          Create a clinic
  GET  /clinics/{clinic_id}                              Get a clinic
  GET  /clinics/{clinic_id}/patients/unresolved-conflicts  Patients with >= N unresolved conflicts
  GET  /clinics/{clinic_id}/reports/conflicts-last-30-days  Conflict counts over a configurable window
"""

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, status

from app.db.collections import clinics
from app.models.clinic import ClinicCreate
from app.services.reporting import (
    conflict_counts_last_n_days,
    patients_with_unresolved_conflicts,
)

router = APIRouter(prefix="/clinics", tags=["clinics"])


# ------------------------------------------------------------------ #
# Clinic CRUD
# ------------------------------------------------------------------ #

@router.get("", tags=["clinics"])
async def list_clinics() -> dict:
    """List all clinics — useful for finding clinic IDs after seeding."""
    result = []
    async for doc in clinics().find({}, sort=[("name", 1)]):
        doc["clinic_id"] = doc.pop("_id")
        result.append(doc)
    return {"total": len(result), "clinics": result}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_clinic(body: ClinicCreate) -> dict:
    """Create a new clinic."""
    existing = await clinics().find_one({"name": body.name})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Clinic '{body.name}' already exists.",
        )
    clinic_id = str(ObjectId())
    doc = {"_id": clinic_id, "name": body.name, "location": body.location}
    await clinics().insert_one(doc)
    return {"clinic_id": clinic_id, "name": body.name}


@router.get("/{clinic_id}")
async def get_clinic(clinic_id: str) -> dict:
    """Get a clinic by ID."""
    doc = await clinics().find_one({"_id": clinic_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")
    doc["clinic_id"] = doc.pop("_id")
    return doc


# ------------------------------------------------------------------ #
# Reporting endpoints
# ------------------------------------------------------------------ #

@router.get("/{clinic_id}/patients/unresolved-conflicts")
async def get_patients_with_unresolved_conflicts(
    clinic_id: str,
    min_conflicts: int = Query(default=1, ge=1, description="Minimum number of unresolved conflicts"),
) -> dict:
    """
    List all patients in a clinic with >= min_conflicts unresolved conflicts.

    Uses a MongoDB aggregation pipeline — no Python-side filtering.
    Results are sorted by conflict count descending.

    Query params:
      min_conflicts (int, default 1) — minimum threshold
    """
    clinic_doc = await clinics().find_one({"_id": clinic_id})
    if clinic_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")

    patients = await patients_with_unresolved_conflicts(
        clinic_id=clinic_id,
        min_conflicts=min_conflicts,
    )

    return {
        "clinic_id": clinic_id,
        "clinic_name": clinic_doc.get("name"),
        "min_conflicts": min_conflicts,
        "matching_patient_count": len(patients),
        "patients": patients,
    }


@router.get("/{clinic_id}/reports/conflicts-last-30-days")
async def get_conflict_report(
    clinic_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    min_conflicts: int = Query(default=2, ge=1, description="Minimum conflicts to include patient"),
) -> dict:
    """
    For the past `days` days, count patients in a clinic with >= min_conflicts.

    Both `days` and `min_conflicts` are configurable via query params.
    The endpoint name uses '30-days' per the assignment spec but the
    window is configurable — the default matches the spec exactly.

    Uses a MongoDB aggregation pipeline — no Python-side filtering.
    """
    clinic_doc = await clinics().find_one({"_id": clinic_id})
    if clinic_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")

    report = await conflict_counts_last_n_days(
        clinic_id=clinic_id,
        days=days,
        min_conflicts=min_conflicts,
    )

    # Enrich with clinic name
    report["clinic_name"] = clinic_doc.get("name")
    return report