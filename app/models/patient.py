from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, Field


# PyObjectId lets us serialise MongoDB's ObjectId as a plain string in JSON
# responses while still validating it properly on input.
PyObjectId = Annotated[str, Field(default_factory=lambda: "")]


class Patient(BaseModel):
    """
    Top-level patient record.

    Kept deliberately slim — demographic details belong in the EMR.
    We only store what we need to query medication snapshots and conflicts.
    """

    id: str = Field(alias="_id", default="")
    name: str
    clinic_id: str                         # denormalised for aggregation queries
    date_of_birth: str                     # ISO date string e.g. "1965-04-12"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"populate_by_name": True}


class PatientCreate(BaseModel):
    """Payload accepted when creating a new patient."""

    name: str
    clinic_id: str
    date_of_birth: str