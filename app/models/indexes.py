"""
Index definitions for all collections.

Called once at application startup (via ensure_indexes in main.py later).
Keeping all index definitions here — rather than scattered across service files —
makes it easy to audit the full indexing strategy in one place.

Rationale per index is documented inline.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # ------------------------------------------------------------------ #
    # patients
    # ------------------------------------------------------------------ #
    # clinic_id: the primary reporting dimension — nearly every aggregation
    # filters or groups by clinic. Without this, every reporting query
    # does a full collection scan.
    await db.patients.create_index("clinic_id")

    # ------------------------------------------------------------------ #
    # medication_snapshots
    # ------------------------------------------------------------------ #
    # (patient_id, source, version): supports the versioning query
    # "what is the latest snapshot for this patient from this source?"
    # and enforces the invariant that (patient_id, source, version) is unique.
    await db.medication_snapshots.create_index(
        [("patient_id", 1), ("source", 1), ("version", 1)],
        unique=True,
        name="patient_source_version_unique",
    )

    # patient_id alone: used by the history endpoint to list all snapshots
    # for a patient regardless of source, sorted by ingested_at.
    await db.medication_snapshots.create_index(
        [("patient_id", 1), ("ingested_at", -1)],
        name="patient_history",
    )

    # clinic_id + ingested_at: used by the 30-day aggregation report.
    # The date range filter runs first (ingested_at), then groups by clinic.
    await db.medication_snapshots.create_index(
        [("clinic_id", 1), ("ingested_at", -1)],
        name="clinic_recent_snapshots",
    )

    # ------------------------------------------------------------------ #
    # conflicts
    # ------------------------------------------------------------------ #
    # (patient_id, status): the most common lookup — "unresolved conflicts
    # for this patient". Compound index because both fields appear together
    # in nearly every query against this collection.
    await db.conflicts.create_index(
        [("patient_id", 1), ("status", 1)],
        name="patient_conflict_status",
    )

    # (clinic_id, status, detected_at): drives both reporting endpoints.
    # clinic_id filters to the clinic, status filters to unresolved,
    # detected_at supports the 30-day window range query.
    await db.conflicts.create_index(
        [("clinic_id", 1), ("status", 1), ("detected_at", -1)],
        name="clinic_conflict_status_date",
    )