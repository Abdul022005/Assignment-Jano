"""
Normalization layer.

All functions here are pure — no database calls, no side effects.
Input: raw MedicationItem (as submitted by the caller)
Output: MedicationItem with canonical fields applied

Rules applied at ingest time:
  1. Drug name  → lowercase, strip leading/trailing whitespace
  2. Dose unit  → lowercase, strip whitespace, expand common abbreviations
  3. Frequency  → lowercase, strip whitespace (free text, not further parsed)
  4. Notes      → strip whitespace only

Nothing is rejected here — malformed values are passed through as-is
after stripping. Validation of required fields is Pydantic's job.
"""

from app.models.medication import MedicationItem

# Common unit aliases → canonical form
_UNIT_ALIASES: dict[str, str] = {
    "mg":        "mg",
    "milligram": "mg",
    "milligrams":"mg",
    "mcg":       "mcg",
    "ug":        "mcg",
    "µg":        "mcg",
    "microgram": "mcg",
    "micrograms":"mcg",
    "g":         "g",
    "gram":      "g",
    "grams":     "g",
    "ml":        "ml",
    "milliliter":"ml",
    "millilitre":"ml",
    "l":         "l",
    "liter":     "l",
    "litre":     "l",
    "units":     "units",
    "unit":      "units",
    "u":         "units",
    "iu":        "iu",
    "meq":       "meq",
    "mmol":      "mmol",
    "%":         "%",
    "percent":   "%",
}


def normalize_unit(raw: str | None) -> str | None:
    """Return the canonical unit string, or None if input is None/empty."""
    if not raw:
        return raw
    cleaned = raw.strip().lower()
    return _UNIT_ALIASES.get(cleaned, cleaned)


def normalize_name(raw: str | None) -> str | None:
    """Return lowercase, whitespace-stripped drug name."""
    if not raw:
        return raw
    return raw.strip().lower()


def normalize_medication_item(item: MedicationItem) -> MedicationItem:
    """
    Return a new MedicationItem with all normalizable fields canonicalized.
    The original item is not mutated.
    """
    return MedicationItem(
        name_canonical=normalize_name(item.name_canonical) or item.name_canonical,
        dose=item.dose,
        unit=normalize_unit(item.unit),
        frequency=item.frequency.strip().lower() if item.frequency else item.frequency,
        status=item.status,
        notes=item.notes.strip() if item.notes else item.notes,
    )


def normalize_medications(items: list[MedicationItem]) -> list[MedicationItem]:
    """Normalize a list of MedicationItems. Returns a new list."""
    return [normalize_medication_item(item) for item in items]