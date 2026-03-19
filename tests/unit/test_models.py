"""
Unit tests for Pydantic document models.

No external dependencies — no database, no HTTP client.
Covers:
  - Happy path parsing for every model
  - Missing required fields
  - Malformed / wrong-type payloads
  - Enum enforcement
  - Edge cases: zero dose, empty strings, empty lists, null optionals
"""

import pytest
from pydantic import ValidationError

from app.models.common import (
    MedicationSource,
    MedicationStatus,
    ConflictType,
    ConflictStatus,
)
from app.models.medication import MedicationItem, MedicationSnapshot, MedicationIngestRequest
from app.models.conflict import Conflict, ConflictResolution, ConflictResolveRequest
from app.models.patient import Patient, PatientCreate
from app.models.clinic import Clinic, ClinicCreate


# ------------------------------------------------------------------ #
# MedicationItem — happy path
# ------------------------------------------------------------------ #

class TestMedicationItemValid:
    def test_parses_full_item(self):
        item = MedicationItem(
            name_canonical="lisinopril",
            dose=10.0,
            unit="mg",
            frequency="once daily",
            status=MedicationStatus.ACTIVE,
            notes="take with food",
        )
        assert item.name_canonical == "lisinopril"
        assert item.dose == 10.0
        assert item.status == MedicationStatus.ACTIVE

    def test_defaults_to_active_status(self):
        assert MedicationItem(name_canonical="metoprolol").status == MedicationStatus.ACTIVE

    def test_all_optional_fields_default_to_none(self):
        item = MedicationItem(name_canonical="aspirin")
        assert item.dose is None
        assert item.unit is None
        assert item.frequency is None
        assert item.notes is None

    def test_zero_dose_is_valid(self):
        # dose=0 is medically meaningful (drug discontinued with explicit zero)
        item = MedicationItem(name_canonical="insulin", dose=0.0, unit="units")
        assert item.dose == 0.0

    def test_integer_dose_coerced_to_float(self):
        item = MedicationItem(name_canonical="furosemide", dose=40, unit="mg")
        assert item.dose == 40.0
        assert isinstance(item.dose, float)

    def test_very_small_dose_accepted(self):
        item = MedicationItem(name_canonical="levothyroxine", dose=0.025, unit="mg")
        assert item.dose == 0.025

    def test_stopped_status_accepted(self):
        item = MedicationItem(name_canonical="warfarin", status=MedicationStatus.STOPPED)
        assert item.status == MedicationStatus.STOPPED


# ------------------------------------------------------------------ #
# MedicationItem — malformed / missing
# ------------------------------------------------------------------ #

class TestMedicationItemInvalid:
    def test_missing_name_canonical_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            MedicationItem(dose=10.0, unit="mg")
        assert any(e["loc"] == ("name_canonical",) for e in exc_info.value.errors())

    def test_invalid_status_string_raises(self):
        with pytest.raises(ValidationError):
            MedicationItem(name_canonical="aspirin", status="discontinued")

    def test_dose_as_non_numeric_string_raises(self):
        with pytest.raises(ValidationError):
            MedicationItem(name_canonical="aspirin", dose="ten")

    def test_negative_dose_stored_as_is(self):
        # Validation of sign belongs in the normalization layer, not the model
        item = MedicationItem(name_canonical="test_drug", dose=-5.0, unit="mg")
        assert item.dose == -5.0


# ------------------------------------------------------------------ #
# MedicationSnapshot
# ------------------------------------------------------------------ #

class TestMedicationSnapshot:
    def test_parses_valid_snapshot(self):
        snap = MedicationSnapshot(
            patient_id="p001",
            clinic_id="clinic_a",
            source=MedicationSource.CLINIC_EMR,
            version=1,
            medications=[MedicationItem(name_canonical="lisinopril", dose=10.0, unit="mg")],
        )
        assert snap.version == 1
        assert len(snap.medications) == 1

    def test_empty_medication_list_is_valid(self):
        snap = MedicaidSnapshot = MedicationSnapshot(
            patient_id="p001",
            clinic_id="clinic_a",
            source=MedicationSource.PATIENT_REPORTED,
            version=1,
        )
        assert snap.medications == []

    def test_ingested_at_auto_populated(self):
        snap = MedicationSnapshot(
            patient_id="p001", clinic_id="clinic_a",
            source=MedicationSource.CLINIC_EMR, version=1,
        )
        assert snap.ingested_at is not None

    def test_missing_patient_id_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            MedicationSnapshot(clinic_id="clinic_a", source=MedicationSource.CLINIC_EMR, version=1)
        assert any(e["loc"] == ("patient_id",) for e in exc_info.value.errors())

    def test_missing_source_raises(self):
        with pytest.raises(ValidationError):
            MedicationSnapshot(patient_id="p001", clinic_id="clinic_a", version=1)

    def test_missing_version_raises(self):
        with pytest.raises(ValidationError):
            MedicationSnapshot(patient_id="p001", clinic_id="clinic_a", source=MedicationSource.CLINIC_EMR)

    def test_invalid_source_string_raises(self):
        with pytest.raises(ValidationError):
            MedicationSnapshot(patient_id="p001", clinic_id="clinic_a", source="pharmacy", version=1)

    def test_version_must_be_integer(self):
        with pytest.raises(ValidationError):
            MedicationSnapshot(patient_id="p001", clinic_id="clinic_a",
                               source=MedicationSource.CLINIC_EMR, version="latest")

    def test_all_three_sources_accepted(self):
        for source in MedicationSource:
            snap = MedicationSnapshot(patient_id="p001", clinic_id="c", source=source, version=1)
            assert snap.source == source

    def test_malformed_medication_inside_list_raises(self):
        with pytest.raises(ValidationError):
            MedicationSnapshot(
                patient_id="p001", clinic_id="clinic_a",
                source=MedicationSource.CLINIC_EMR, version=1,
                medications=[{"dose": 10.0}],   # missing name_canonical
            )


# ------------------------------------------------------------------ #
# MedicationIngestRequest
# ------------------------------------------------------------------ #

class TestMedicationIngestRequest:
    def test_valid_request(self):
        req = MedicationIngestRequest(
            source=MedicationSource.HOSPITAL_DISCHARGE,
            medications=[MedicationItem(name_canonical="furosemide", dose=40.0, unit="mg")],
        )
        assert len(req.medications) == 1

    def test_empty_medications_list_accepted(self):
        req = MedicationIngestRequest(source=MedicationSource.CLINIC_EMR, medications=[])
        assert req.medications == []

    def test_missing_source_raises(self):
        with pytest.raises(ValidationError):
            MedicationIngestRequest(medications=[])

    def test_medications_not_a_list_raises(self):
        with pytest.raises(ValidationError):
            MedicationIngestRequest(source=MedicationSource.CLINIC_EMR, medications="lisinopril 10mg")

    def test_malformed_item_inside_list_raises(self):
        with pytest.raises(ValidationError):
            MedicationIngestRequest(
                source=MedicationSource.CLINIC_EMR,
                medications=[{"dose": 10.0}],   # missing name_canonical
            )


# ------------------------------------------------------------------ #
# ConflictResolution
# ------------------------------------------------------------------ #

class TestConflictResolution:
    def test_valid_resolution(self):
        res = ConflictResolution(
            chosen_source=MedicationSource.CLINIC_EMR,
            reason="EMR dose confirmed by prescribing physician",
            resolved_by="dr_smith",
        )
        assert res.chosen_source == MedicationSource.CLINIC_EMR

    def test_chosen_source_can_be_none(self):
        res = ConflictResolution(chosen_source=None, reason="updated in EMR", resolved_by="dr_jones")
        assert res.chosen_source is None

    def test_resolved_at_auto_populated(self):
        res = ConflictResolution(reason="confirmed", resolved_by="nurse_ali")
        assert res.resolved_at is not None

    def test_missing_reason_raises(self):
        with pytest.raises(ValidationError):
            ConflictResolution(resolved_by="dr_smith")

    def test_missing_resolved_by_raises(self):
        with pytest.raises(ValidationError):
            ConflictResolution(reason="confirmed")


# ------------------------------------------------------------------ #
# Conflict
# ------------------------------------------------------------------ #

class TestConflict:
    def _base(self, **kwargs):
        defaults = dict(
            patient_id="p001", clinic_id="clinic_a",
            conflict_type=ConflictType.DOSE_MISMATCH,
            drug_names=["lisinopril"],
            sources_involved=[MedicationSource.CLINIC_EMR, MedicationSource.HOSPITAL_DISCHARGE],
            snapshot_ids=["snap_1", "snap_2"],
            detail="Dose 10mg in clinic_emr vs 20mg in hospital_discharge",
        )
        defaults.update(kwargs)
        return Conflict(**defaults)

    def test_defaults_to_unresolved(self):
        c = self._base()
        assert c.status == ConflictStatus.UNRESOLVED
        assert c.resolution is None

    def test_all_conflict_types_accepted(self):
        for ctype in ConflictType:
            c = self._base(conflict_type=ctype)
            assert c.conflict_type == ctype

    def test_missing_patient_id_raises(self):
        with pytest.raises(ValidationError):
            Conflict(
                clinic_id="clinic_a", conflict_type=ConflictType.DOSE_MISMATCH,
                drug_names=["x"], sources_involved=[MedicationSource.CLINIC_EMR],
                snapshot_ids=["s1"], detail="test",
            )

    def test_missing_detail_raises(self):
        with pytest.raises(ValidationError):
            Conflict(
                patient_id="p001", clinic_id="clinic_a",
                conflict_type=ConflictType.DOSE_MISMATCH,
                drug_names=["x"], sources_involved=[MedicationSource.CLINIC_EMR],
                snapshot_ids=["s1"],
            )

    def test_empty_drug_names_accepted(self):
        # class_conflict can describe drugs by class, not name
        c = self._base(conflict_type=ConflictType.CLASS_CONFLICT, drug_names=[])
        assert c.drug_names == []

    def test_detected_at_auto_populated(self):
        assert self._base().detected_at is not None


# ------------------------------------------------------------------ #
# ConflictResolveRequest
# ------------------------------------------------------------------ #

class TestConflictResolveRequest:
    def test_valid_request(self):
        req = ConflictResolveRequest(
            chosen_source=MedicationSource.CLINIC_EMR,
            reason="Confirmed with physician",
            resolved_by="dr_smith",
        )
        assert req.chosen_source == MedicationSource.CLINIC_EMR

    def test_chosen_source_optional(self):
        req = ConflictResolveRequest(reason="Neither correct", resolved_by="dr_jones")
        assert req.chosen_source is None

    def test_missing_reason_raises(self):
        with pytest.raises(ValidationError):
            ConflictResolveRequest(resolved_by="dr_smith")

    def test_missing_resolved_by_raises(self):
        with pytest.raises(ValidationError):
            ConflictResolveRequest(reason="confirmed")


# ------------------------------------------------------------------ #
# PatientCreate
# ------------------------------------------------------------------ #

class TestPatientCreate:
    def test_valid_patient(self):
        p = PatientCreate(name="Jane Doe", clinic_id="clinic_a", date_of_birth="1965-04-12")
        assert p.name == "Jane Doe"

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            PatientCreate(clinic_id="clinic_a", date_of_birth="1965-04-12")

    def test_missing_clinic_id_raises(self):
        with pytest.raises(ValidationError):
            PatientCreate(name="Jane Doe", date_of_birth="1965-04-12")

    def test_missing_dob_raises(self):
        with pytest.raises(ValidationError):
            PatientCreate(name="Jane Doe", clinic_id="clinic_a")


# ------------------------------------------------------------------ #
# ClinicCreate
# ------------------------------------------------------------------ #

class TestClinicCreate:
    def test_valid_clinic(self):
        c = ClinicCreate(name="Kochi Dialysis Center", location="Kochi, Kerala")
        assert c.name == "Kochi Dialysis Center"

    def test_location_is_optional(self):
        assert ClinicCreate(name="City Clinic").location is None

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            ClinicCreate(location="somewhere")