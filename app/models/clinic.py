from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Clinic(BaseModel):
    """
    A dialysis clinic.

    Patients and snapshots store clinic_id as a denormalised string.
    This collection is the source of truth for clinic names and metadata,
    and is used by the reporting endpoints to validate clinic_id values.
    """

    id: str = Field(alias="_id", default="")
    name: str
    location: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"populate_by_name": True}


class ClinicCreate(BaseModel):
    name: str
    location: str | None = None