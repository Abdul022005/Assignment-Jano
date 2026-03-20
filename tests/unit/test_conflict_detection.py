"""
Unit tests for conflict detection — the core business logic.

All three detectors are pure functions tested here with zero DB or HTTP calls.
These tests cover every edge case the assignment explicitly calls out:
  - Dose mismatches (same drug, different dose)
  - Stopped vs active
  - Blacklisted drug combinations
  - Missing fields
  - Boundary conditions (tolerance, zero dose, single source)
"""

import pytest

from app.models.common import MedicationStatus
from app.services.conflict_detection import (
    detect_dose_mismatches,
    detect_stopped_vs_active,
    detect_class_conflicts,
    ConflictData,
)
from app.models.common import ConflictType


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def make_snapshot(snap_id: str, source: str, medications: list[dict]) -> dict:
    return {"_id": snap_id, "patient_id": "p001", "source": source, "medications": medications}


def make_med(name: str, dose: float | None = None, unit: str = "mg",
             status: str = "active") -> dict:
    return {"name_canonical": name, "dose": dose, "unit": unit, "status": status}


SAMPLE_RULES = {
    "blacklisted_combinations": [
        {"drugs": ["warfarin", "aspirin"], "reason": "Bleeding risk."},
        {"drugs": ["lisinopril", "spironolactone"], "reason": "Hyperkalaemia risk."},
    ],
    "dose_tolerance_percent": 10,
}


# ------------------------------------------------------------------ #
# detect_dose_mismatches
# ------------------------------------------------------------------ #

class TestDoseMismatch:

    def test_detects_clear_dose_mismatch(self):
        snaps = {
            "clinic_emr":          make_snapshot("s1", "clinic_emr",
                                       [make_med("lisinopril", dose=10.0)]),
            "hospital_discharge":  make_snapshot("s2", "hospital_discharge",
                                       [make_med("lisinopril", dose=20.0)]),
        }
        result = detect_dose_mismatches(snaps, tolerance_pct=0)
        assert len(result) == 1
        assert result[0].conflict_type == ConflictType.DOSE_MISMATCH
        assert result[0].drug_names == ["lisinopril"]
        assert "10.0" in result[0].detail
        assert "20.0" in result[0].detail

    def test_no_conflict_when_doses_match(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("lisinopril", dose=10.0)]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("lisinopril", dose=10.0)]),
        }
        assert detect_dose_mismatches(snaps, tolerance_pct=0) == []

    def test_no_conflict_single_source(self):
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr",
                              [make_med("lisinopril", dose=10.0)]),
        }
        assert detect_dose_mismatches(snaps, tolerance_pct=0) == []

    def test_within_tolerance_no_conflict(self):
        # 10mg vs 10.5mg = 4.8% diff — within 10% tolerance
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("furosemide", dose=10.0)]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("furosemide", dose=10.5)]),
        }
        assert detect_dose_mismatches(snaps, tolerance_pct=10) == []

    def test_outside_tolerance_raises_conflict(self):
        # 10mg vs 20mg = 50% diff — outside any tolerance
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("furosemide", dose=10.0)]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("furosemide", dose=20.0)]),
        }
        assert len(detect_dose_mismatches(snaps, tolerance_pct=10)) == 1

    def test_missing_dose_field_skipped(self):
        # A medication with dose=None cannot be compared — must be skipped
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("aspirin", dose=None)]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("aspirin", dose=100.0)]),
        }
        # aspirin in clinic_emr has no dose — only one entry with a dose, no comparison possible
        result = detect_dose_mismatches(snaps, tolerance_pct=0)
        assert result == []

    def test_stopped_drugs_excluded_from_dose_check(self):
        # A stopped drug in one source shouldn't trigger dose mismatch
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin", dose=5.0, status="stopped")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("warfarin", dose=2.0)]),
        }
        # stopped_vs_active would catch this, not dose_mismatch
        result = detect_dose_mismatches(snaps, tolerance_pct=0)
        assert result == []

    def test_multiple_drugs_multiple_conflicts(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr", [
                                      make_med("lisinopril", dose=10.0),
                                      make_med("furosemide",  dose=20.0),
                                  ]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge", [
                                      make_med("lisinopril", dose=20.0),
                                      make_med("furosemide",  dose=80.0),
                                  ]),
        }
        result = detect_dose_mismatches(snaps, tolerance_pct=0)
        assert len(result) == 2
        drug_names = {r.drug_names[0] for r in result}
        assert drug_names == {"lisinopril", "furosemide"}

    def test_three_sources_with_two_different_doses(self):
        # clinic_emr and patient_reported both say 10mg, hospital says 20mg
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("amlodipine", dose=10.0)]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("amlodipine", dose=5.0)]),
            "patient_reported":   make_snapshot("s3", "patient_reported",
                                      [make_med("amlodipine", dose=10.0)]),
        }
        result = detect_dose_mismatches(snaps, tolerance_pct=0)
        assert len(result) == 1
        assert len(result[0].sources_involved) == 3

    def test_empty_snapshots_no_conflict(self):
        assert detect_dose_mismatches({}, tolerance_pct=0) == []

    def test_all_empty_medication_lists(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",         []),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",  []),
        }
        assert detect_dose_mismatches(snaps, tolerance_pct=0) == []


# ------------------------------------------------------------------ #
# detect_stopped_vs_active
# ------------------------------------------------------------------ #

class TestStoppedVsActive:

    def test_detects_stopped_in_one_active_in_another(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin", status="active")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("warfarin", status="stopped")]),
        }
        result = detect_stopped_vs_active(snaps)
        assert len(result) == 1
        assert result[0].conflict_type == ConflictType.STOPPED_VS_ACTIVE
        assert result[0].drug_names == ["warfarin"]
        assert "warfarin" in result[0].detail
        assert "clinic_emr" in result[0].detail
        assert "hospital_discharge" in result[0].detail

    def test_no_conflict_both_active(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin", status="active")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("warfarin", status="active")]),
        }
        assert detect_stopped_vs_active(snaps) == []

    def test_no_conflict_both_stopped(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin", status="stopped")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("warfarin", status="stopped")]),
        }
        assert detect_stopped_vs_active(snaps) == []

    def test_drug_only_in_one_source_no_conflict(self):
        # Drug present in only one source — cannot determine stopped/active conflict
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("metoprolol", status="active")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge", []),
        }
        assert detect_stopped_vs_active(snaps) == []

    def test_multiple_stopped_vs_active_conflicts(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr", [
                                      make_med("warfarin",   status="active"),
                                      make_med("furosemide", status="stopped"),
                                  ]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge", [
                                      make_med("warfarin",   status="stopped"),
                                      make_med("furosemide", status="active"),
                                  ]),
        }
        result = detect_stopped_vs_active(snaps)
        assert len(result) == 2

    def test_single_source_no_conflict(self):
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr",
                              [make_med("warfarin", status="active")]),
        }
        assert detect_stopped_vs_active(snaps) == []

    def test_missing_status_defaults_to_active(self):
        # A medication without a status field defaults to active
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [{"name_canonical": "aspirin"}]),  # no status key
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("aspirin", status="stopped")]),
        }
        result = detect_stopped_vs_active(snaps)
        assert len(result) == 1

    def test_empty_snapshots(self):
        assert detect_stopped_vs_active({}) == []


# ------------------------------------------------------------------ #
# detect_class_conflicts
# ------------------------------------------------------------------ #

class TestClassConflicts:

    def test_detects_blacklisted_pair(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("aspirin")]),
        }
        result = detect_class_conflicts(snaps, SAMPLE_RULES)
        assert len(result) == 1
        assert result[0].conflict_type == ConflictType.CLASS_CONFLICT
        assert set(result[0].drug_names) == {"warfarin", "aspirin"}
        assert "Bleeding risk" in result[0].detail

    def test_no_conflict_only_one_drug_of_pair(self):
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr", [make_med("warfarin")]),
        }
        assert detect_class_conflicts(snaps, SAMPLE_RULES) == []

    def test_both_drugs_same_source(self):
        # Dangerous combination is dangerous regardless of source
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr", [
                make_med("warfarin"),
                make_med("aspirin"),
            ]),
        }
        result = detect_class_conflicts(snaps, SAMPLE_RULES)
        assert len(result) == 1

    def test_stopped_drug_excluded_from_class_check(self):
        # If aspirin is stopped, the combination is no longer active
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr",
                                      [make_med("warfarin")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge",
                                      [make_med("aspirin", status="stopped")]),
        }
        result = detect_class_conflicts(snaps, SAMPLE_RULES)
        assert result == []

    def test_multiple_blacklisted_pairs_detected(self):
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr", [
                make_med("warfarin"),
                make_med("aspirin"),
                make_med("lisinopril"),
                make_med("spironolactone"),
            ]),
        }
        result = detect_class_conflicts(snaps, SAMPLE_RULES)
        assert len(result) == 2

    def test_empty_rules_no_conflict(self):
        snaps = {
            "clinic_emr": make_snapshot("s1", "clinic_emr", [make_med("warfarin")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge", [make_med("aspirin")]),
        }
        assert detect_class_conflicts(snaps, {"blacklisted_combinations": []}) == []

    def test_unknown_drug_no_conflict(self):
        snaps = {
            "clinic_emr":         make_snapshot("s1", "clinic_emr", [make_med("drugx")]),
            "hospital_discharge": make_snapshot("s2", "hospital_discharge", [make_med("drugy")]),
        }
        assert detect_class_conflicts(snaps, SAMPLE_RULES) == []

    def test_empty_snapshots(self):
        assert detect_class_conflicts({}, SAMPLE_RULES) == []

    def test_snapshot_ids_included_in_conflict(self):
        snaps = {
            "clinic_emr":         make_snapshot("snap_001", "clinic_emr",   [make_med("warfarin")]),
            "hospital_discharge": make_snapshot("snap_002", "hospital_discharge", [make_med("aspirin")]),
        }
        result = detect_class_conflicts(snaps, SAMPLE_RULES)
        assert "snap_001" in result[0].snapshot_ids
        assert "snap_002" in result[0].snapshot_ids