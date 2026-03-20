"""
Ingestion service.

Responsible for:
  1. Normalizing incoming medication items
  2. Determining the next version number for (patient_id, source)
  3. Persisting the new MedicationSnapshot
  4. Returning the saved snapshot

Conflict detection is intentionally NOT triggered here — it lives in its
own service (conflict_detection.py, Stage 4) and is called by the route
handler after ingestion completes. This keeps each service focused on one job.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.db.collections import medication_snapshots, patients
from app.models.common import MedicationSource
from app.models.medication import MedicationItem, MedicationSnapshot
from app.services.normalization import normalize_medications


class PatientNotFoundError(Exception):
    """Raised when the patient_id does not exist in the patients collection."""
    pass


async def get_next_version(patient_id: str, source: MedicationSource) -> int:
    """
    Return the next version number for this (patient_id, source) pair.

    Queries for the highest existing version and returns max + 1.
    Returns 1 if no prior snapshot exists for this source.
    """
    collection = medication_snapshots()
    latest = await collection.find_one(
        {"patient_id": patient_id, "source": source.value},
        sort=[("version", -1)],
        projection={"version": 1},
    )
    if latest is None:
        return 1
    return latest["version"] + 1


async def ingest_medications(
    patient_id: str,
    source: MedicationSource,
    raw_medications: list[MedicationItem],
) -> MedicationSnapshot:
    """
    Normalize, version, and persist a new MedicationSnapshot.

    Raises PatientNotFoundError if patient_id does not exist.
    Returns the saved MedicationSnapshot with its _id populated.
    """
    # Verify the patient exists before writing anything
    patient_doc = await patients().find_one({"_id": patient_id})
    if patient_doc is None:
        raise PatientNotFoundError(f"Patient '{patient_id}' not found.")

    # Normalize medication items
    normalized = normalize_medications(raw_medications)

    # Determine next version
    version = await get_next_version(patient_id, source)

    # Build the snapshot document
    snapshot_id = str(ObjectId())
    snapshot = MedicationSnapshot(
        _id=snapshot_id,
        patient_id=patient_id,
        clinic_id=patient_doc["clinic_id"],   # pulled from patient record
        source=source,
        version=version,
        medications=normalized,
        ingested_at=datetime.now(timezone.utc),
    )

    # Persist — use model_dump with by_alias=True so "_id" is used, not "id"
    doc = snapshot.model_dump(by_alias=True, mode="json")
    await medication_snapshots().insert_one(doc)

    return snapshot