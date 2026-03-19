"""
Patients router.

Endpoints:
  POST /patients                              Create a patient
  GET  /patients/{patient_id}                 Get a patient
  POST /patients/{patient_id}/medications     Ingest a medication list
  GET  /patients/{patient_id}/medications/history  View snapshot history
"""

from bson import ObjectId
from fastapi import APIRouter, HTTPException, status

from app.db.collections import medication_snapshots, patients
from app.models.medication import MedicationIngestRequest, MedicationSnapshot
from app.models.patient import Patient, PatientCreate
from app.services.ingestion import PatientNotFoundError, ingest_medications

router = APIRouter(prefix="/patients", tags=["patients"])


# ------------------------------------------------------------------ #
# Patient CRUD
# ------------------------------------------------------------------ #

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_patient(body: PatientCreate) -> dict:
    """Create a new patient record."""
    existing = await patients().find_one({"name": body.name, "clinic_id": body.clinic_id})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A patient named '{body.name}' already exists in clinic '{body.clinic_id}'.",
        )

    patient_id = str(ObjectId())
    doc = {
        "_id": patient_id,
        "name": body.name,
        "clinic_id": body.clinic_id,
        "date_of_birth": body.date_of_birth,
    }
    await patients().insert_one(doc)
    return {"patient_id": patient_id, "name": body.name, "clinic_id": body.clinic_id}


@router.get("/{patient_id}")
async def get_patient(patient_id: str) -> dict:
    """Retrieve a patient by ID."""
    doc = await patients().find_one({"_id": patient_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    doc["patient_id"] = doc.pop("_id")
    return doc


# ------------------------------------------------------------------ #
# Medication ingestion
# ------------------------------------------------------------------ #

@router.post("/{patient_id}/medications", status_code=status.HTTP_201_CREATED)
async def ingest_medication_list(
    patient_id: str,
    body: MedicationIngestRequest,
) -> dict:
    """
    Ingest a medication list from one source for a given patient.

    Creates a new immutable snapshot (append-only versioning).
    Conflict detection will be triggered automatically in Stage 4.

    Returns the snapshot_id, version number, and medication count.
    """
    try:
        snapshot = await ingest_medications(
            patient_id=patient_id,
            source=body.source,
            raw_medications=body.medications,
        )
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return {
        "snapshot_id": snapshot.id,
        "patient_id": snapshot.patient_id,
        "source": snapshot.source,
        "version": snapshot.version,
        "medication_count": len(snapshot.medications),
        "ingested_at": snapshot.ingested_at.isoformat(),
    }


# ------------------------------------------------------------------ #
# Medication history
# ------------------------------------------------------------------ #

@router.get("/{patient_id}/medications/history")
async def get_medication_history(
    patient_id: str,
    source: str | None = None,
) -> dict:
    """
    Return all medication snapshots for a patient, newest first.

    Optionally filter by source using ?source=clinic_emr
    """
    # Verify patient exists
    patient_doc = await patients().find_one({"_id": patient_id})
    if patient_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    query: dict = {"patient_id": patient_id}
    if source:
        query["source"] = source

    cursor = medication_snapshots().find(query, sort=[("ingested_at", -1)])
    snapshots = []
    async for doc in cursor:
        doc["snapshot_id"] = doc.pop("_id")
        snapshots.append(doc)

    return {
        "patient_id": patient_id,
        "total": len(snapshots),
        "snapshots": snapshots,
    }