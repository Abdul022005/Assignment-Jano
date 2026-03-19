from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.models.common import ConflictType, ConflictStatus, MedicationSource


class ConflictResolution(BaseModel):
    """
    Embedded sub-document recorded when a clinician resolves a conflict.

    Design decision — no automatic resolution:
      Conflicts are only resolved by an explicit human action. The chosen_source
      field records which source's version the clinician accepted, alongside a
      free-text reason. This creates an auditable record safe for clinical use.
    """

    chosen_source: MedicationSource | None = None   # which source was accepted
    reason: str                                      # free-text clinical rationale
    resolved_by: str                                 # clinician identifier
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Conflict(BaseModel):
    """
    A detected disagreement between two or more medication sources for one patient.

    `snapshot_ids` references the specific snapshots involved, giving a stable
    link back to exactly what was being compared when the conflict was detected.

    `drug_names` is a denormalised list of the canonical drug names involved,
    stored here so reporting queries can filter by drug without a lookup.
    """

    id: str = Field(alias="_id", default="")
    patient_id: str
    clinic_id: str                                   # denormalised for aggregation
    conflict_type: ConflictType
    status: ConflictStatus = ConflictStatus.UNRESOLVED
    drug_names: list[str]                            # canonical names of involved drugs
    sources_involved: list[MedicationSource]         # which sources disagree
    snapshot_ids: list[str]                          # references to MedicationSnapshot._id
    detail: str                                      # human-readable description
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolution: ConflictResolution | None = None     # None until resolved

    model_config = {"populate_by_name": True}


class ConflictResolveRequest(BaseModel):
    """Payload for PATCH /conflicts/{conflict_id}/resolve."""

    chosen_source: MedicationSource | None = None
    reason: str
    resolved_by: str