from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.models.common import MedicationSource, MedicationStatus


class MedicationItem(BaseModel):
    """
    A single drug entry within a snapshot.

    `name_canonical` is the normalised form produced at ingest time
    (lowercase, whitespace-stripped). Raw names are not stored.
    """

    name_canonical: str                    # e.g. "lisinopril"
    dose: float | None = None             # numeric value, e.g. 10.0
    unit: str | None = None               # normalised unit, e.g. "mg"
    frequency: str | None = None          # e.g. "once daily" — free text, not parsed
    status: MedicationStatus = MedicationStatus.ACTIVE
    notes: str | None = None


class MedicationSnapshot(BaseModel):
    """
    An immutable record of what one source reported at one point in time.

    Design decision — append-only versioning:
      Every ingest creates a NEW snapshot; existing snapshots are never mutated.
      `version` is a monotonically incrementing integer scoped to (patient_id, source).
      This gives a full audit trail: you can reconstruct the medication list as seen
      by any source at any past timestamp.

    Trade-off:
      Document count grows with every ingest. Acceptable here given the low frequency
      of dialysis patient record updates (typically once per clinic visit).
    """

    id: str = Field(alias="_id", default="")
    patient_id: str
    clinic_id: str                         # denormalised — avoids a join in reporting
    source: MedicationSource
    version: int                           # 1-based, scoped to (patient_id, source)
    medications: list[MedicationItem] = Field(default_factory=list)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"populate_by_name": True}


class MedicationIngestRequest(BaseModel):
    """Payload for POST /patients/{patient_id}/medications."""

    source: MedicationSource
    medications: list[MedicationItem]