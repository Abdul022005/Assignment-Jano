"""
Unit tests for the normalization service.

Pure functions — no DB, no HTTP.
These are the easiest tests to write and the most valuable:
if normalization is wrong, conflict detection will miss matches.
"""

import pytest

from app.models.common import MedicationStatus
from app.models.medication import MedicationItem
from app.services.normalization import (
    normalize_name,
    normalize_unit,
    normalize_medication_item,
    normalize_medications,
)


# ------------------------------------------------------------------ #
# normalize_name
# ------------------------------------------------------------------ #

class TestNormalizeName:
    def test_lowercases(self):
        assert normalize_name("Lisinopril") == "lisinopril"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_name("  aspirin  ") == "aspirin"

    def test_lowercases_and_strips(self):
        assert normalize_name("  METOPROLOL  ") == "metoprolol"

    def test_internal_whitespace_preserved(self):
        # Multi-word drug names keep internal spaces
        assert normalize_name("Sodium Chloride") == "sodium chloride"

    def test_none_returns_none(self):
        assert normalize_name(None) is None

    def test_empty_string_returns_empty(self):
        assert normalize_name("") == ""

    def test_already_canonical_unchanged(self):
        assert normalize_name("furosemide") == "furosemide"


# ------------------------------------------------------------------ #
# normalize_unit
# ------------------------------------------------------------------ #

class TestNormalizeUnit:
    def test_mg_uppercase(self):
        assert normalize_unit("MG") == "mg"

    def test_mg_mixed_case(self):
        assert normalize_unit("Mg") == "mg"

    def test_mcg_alias_ug(self):
        assert normalize_unit("ug") == "mcg"

    def test_mcg_alias_unicode(self):
        assert normalize_unit("µg") == "mcg"

    def test_mcg_full_word(self):
        assert normalize_unit("micrograms") == "mcg"

    def test_milligram_full_word(self):
        assert normalize_unit("milligram") == "mg"

    def test_milligrams_plural(self):
        assert normalize_unit("milligrams") == "mg"

    def test_units_alias_u(self):
        assert normalize_unit("u") == "units"

    def test_units_alias_unit_singular(self):
        assert normalize_unit("unit") == "units"

    def test_percent_symbol(self):
        assert normalize_unit("%") == "%"

    def test_percent_word(self):
        assert normalize_unit("percent") == "%"

    def test_unknown_unit_lowercased_and_returned(self):
        # Unknown units are passed through lowercased — not rejected
        assert normalize_unit("TABLET") == "tablet"

    def test_none_returns_none(self):
        assert normalize_unit(None) is None

    def test_empty_string_returned_as_is(self):
        assert normalize_unit("") == ""

    def test_strips_whitespace(self):
        assert normalize_unit("  mg  ") == "mg"


# ------------------------------------------------------------------ #
# normalize_medication_item
# ------------------------------------------------------------------ #

class TestNormalizeMedicationItem:
    def test_normalizes_name_and_unit(self):
        item = MedicationItem(name_canonical="LISINOPRIL", dose=10.0, unit="MG")
        result = normalize_medication_item(item)
        assert result.name_canonical == "lisinopril"
        assert result.unit == "mg"

    def test_does_not_mutate_original(self):
        item = MedicationItem(name_canonical="ASPIRIN", unit="MG")
        _ = normalize_medication_item(item)
        assert item.name_canonical == "ASPIRIN"   # original unchanged

    def test_normalizes_frequency(self):
        item = MedicationItem(name_canonical="metoprolol", frequency="  Twice Daily  ")
        result = normalize_medication_item(item)
        assert result.frequency == "twice daily"

    def test_strips_notes_whitespace(self):
        item = MedicationItem(name_canonical="warfarin", notes="  take at night  ")
        result = normalize_medication_item(item)
        assert result.notes == "take at night"

    def test_preserves_dose(self):
        item = MedicationItem(name_canonical="furosemide", dose=40.0, unit="mg")
        result = normalize_medication_item(item)
        assert result.dose == 40.0

    def test_preserves_status(self):
        item = MedicationItem(name_canonical="warfarin", status=MedicationStatus.STOPPED)
        result = normalize_medication_item(item)
        assert result.status == MedicationStatus.STOPPED

    def test_none_unit_stays_none(self):
        item = MedicationItem(name_canonical="aspirin")
        result = normalize_medication_item(item)
        assert result.unit is None

    def test_none_frequency_stays_none(self):
        item = MedicationItem(name_canonical="aspirin")
        result = normalize_medication_item(item)
        assert result.frequency is None

    def test_mcg_alias_normalized(self):
        item = MedicationItem(name_canonical="levothyroxine", dose=0.025, unit="µg")
        result = normalize_medication_item(item)
        assert result.unit == "mcg"


# ------------------------------------------------------------------ #
# normalize_medications (list)
# ------------------------------------------------------------------ #

class TestNormalizeMedications:
    def test_normalizes_all_items(self):
        items = [
            MedicationItem(name_canonical="ASPIRIN", unit="MG"),
            MedicationItem(name_canonical="WARFARIN", unit="MG"),
        ]
        results = normalize_medications(items)
        assert results[0].name_canonical == "aspirin"
        assert results[1].name_canonical == "warfarin"

    def test_empty_list_returns_empty(self):
        assert normalize_medications([]) == []

    def test_returns_new_list(self):
        items = [MedicationItem(name_canonical="aspirin")]
        results = normalize_medications(items)
        assert results is not items

    def test_single_item_list(self):
        items = [MedicationItem(name_canonical="  Furosemide  ", unit="MG")]
        results = normalize_medications(items)
        assert results[0].name_canonical == "furosemide"
        assert results[0].unit == "mg"