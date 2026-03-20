"""
Patients router.

Endpoints:
  POST /patients                                   Create a patient
  GET  /patients/{patient_id}                      Get a patient
  POST /patients/{patient_id}/medications          Ingest a medication list
  GET  /patients/{patient_id}/medications/history  View snapshot history
  GET  /patients/{patient_id}/conflicts            List conflicts for a patient

Route ordering matters — specific paths (/{id}/medications/history,
/{id}/conflicts) must be defined BEFORE the generic /{patient_id} catch-all,
otherwise FastAPI matches the catch-all first.
"""

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, status

from app.db.collections import conflicts, medication_snapshots, patients
from app.models.common import ConflictStatus
from app.models.medication import MedicationIngestRequest
from app.models.patient import PatientCreate
from app.services.conflict_detection import run_conflict_detection
from app.services.ingestion import PatientNotFoundError, ingest_medications

router = APIRouter(prefix="/patients", tags=["patients"])


# ------------------------------------------------------------------ #
# Create patient
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

    Creates a new immutable snapshot (append-only versioning),
    then immediately runs conflict detection across all available sources.
    """
    try:
        snapshot = await ingest_medications(
            patient_id=patient_id,
            source=body.source,
            raw_medications=body.medications,
        )
    except PatientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    new_conflicts = await run_conflict_detection(
        patient_id=patient_id,
        clinic_id=snapshot.clinic_id,
    )

    return {
        "snapshot_id": snapshot.id,
        "patient_id": snapshot.patient_id,
        "source": snapshot.source,
        "version": snapshot.version,
        "medication_count": len(snapshot.medications),
        "ingested_at": snapshot.ingested_at.isoformat(),
        "conflicts_detected": len(new_conflicts),
        "conflict_ids": [c.id for c in new_conflicts],
    }


# ------------------------------------------------------------------ #
# Medication history
# ------------------------------------------------------------------ #

@router.get("/{patient_id}/medications/history")
async def get_medication_history(
    patient_id: str,
    source: str | None = None,
) -> dict:
    """Return all snapshots for a patient, newest first. Filter by ?source="""
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

    return {"patient_id": patient_id, "total": len(snapshots), "snapshots": snapshots}


# ------------------------------------------------------------------ #
# Conflicts for a patient
# ------------------------------------------------------------------ #

@router.get("/{patient_id}/conflicts")
async def get_patient_conflicts(
    patient_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
) -> dict:
    """List all conflicts for a patient. Filter by ?status=unresolved or ?status=resolved"""
    patient_doc = await patients().find_one({"_id": patient_id})
    if patient_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    query: dict = {"patient_id": patient_id}
    if status_filter:
        if status_filter not in [s.value for s in ConflictStatus]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status '{status_filter}'. Must be 'unresolved' or 'resolved'.",
            )
        query["status"] = status_filter

    cursor = conflicts().find(query, sort=[("detected_at", -1)])
    result = []
    async for doc in cursor:
        doc["conflict_id"] = doc.pop("_id")
        result.append(doc)

    return {"patient_id": patient_id, "total": len(result), "conflicts": result}


# ------------------------------------------------------------------ #
# Get patient by ID — must be LAST to avoid swallowing specific routes
# ------------------------------------------------------------------ #

@router.get("/{patient_id}")
async def get_patient(patient_id: str) -> dict:
    """Retrieve a patient by ID."""
    doc = await patients().find_one({"_id": patient_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    doc["patient_id"] = doc.pop("_id")
    return doc