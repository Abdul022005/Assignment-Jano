"""
Conflict detection service.

Responsible for:
  1. Loading conflict rules from the static JSON file
  2. Fetching the latest snapshot per source for a patient
  3. Running three detectors against those snapshots:
       a. Dose mismatch      — same drug, different dose across sources
       b. Stopped vs active  — drug marked stopped in one source, active in another
       c. Class conflict     — two drugs from a blacklisted combination both present
  4. Deduplicating against already-open conflicts (no duplicate unresolved records)
  5. Persisting new Conflict documents and returning them

Design notes:
  - All three detectors are pure functions (no DB calls).
    They receive snapshot data as plain dicts and return ConflictData objects.
    This makes them trivially unit-testable without mocking anything.
  - DB interaction is isolated to two async functions: fetch_latest_snapshots()
    and save_conflicts().
  - Called by the ingestion route handler after a snapshot is saved.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

from bson import ObjectId

from app.db.collections import conflicts, medication_snapshots
from app.models.common import ConflictType, ConflictStatus, MedicationSource, MedicationStatus
from app.models.conflict import Conflict

# ------------------------------------------------------------------ #
# Rules loading
# ------------------------------------------------------------------ #

_RULES_PATH = Path(__file__).parent / "conflict_rules.json"


def load_rules() -> dict:
    with open(_RULES_PATH) as f:
        return json.load(f)


# Cached at module level — file is read once per process
_RULES: dict = load_rules()


# ------------------------------------------------------------------ #
# Internal data structure for detected conflicts (pre-persistence)
# ------------------------------------------------------------------ #

@dataclass
class ConflictData:
    conflict_type: ConflictType
    drug_names: list[str]
    sources_involved: list[MedicationSource]
    snapshot_ids: list[str]
    detail: str


# ------------------------------------------------------------------ #
# Pure detector functions
# ------------------------------------------------------------------ #

def detect_dose_mismatches(
    snapshots: dict[str, dict],
    tolerance_pct: float = 0.0,
) -> list[ConflictData]:
    """
    Detect cases where the same drug appears in multiple sources with different doses.

    snapshots: { source_name: snapshot_doc }
    tolerance_pct: percentage difference allowed before flagging (from rules file).

    Logic:
      Build a map of { drug_name: [(source, dose, snapshot_id), ...] }.
      For each drug that appears in ≥2 sources, check whether all doses are
      within tolerance of each other. If not, emit a DOSE_MISMATCH conflict.
    """
    # drug_name -> list of (source, dose, unit, snapshot_id)
    drug_map: dict[str, list[tuple]] = {}

    for source, snap in snapshots.items():
        snap_id = snap.get("_id", "")
        for med in snap.get("medications", []):
            if med.get("status") == MedicationStatus.STOPPED.value:
                continue  # stopped drugs are handled by stopped_vs_active detector
            name = med.get("name_canonical", "")
            dose = med.get("dose")
            unit = med.get("unit")
            if name and dose is not None:
                drug_map.setdefault(name, []).append((source, dose, unit, snap_id))

    detected = []
    for drug_name, entries in drug_map.items():
        if len(entries) < 2:
            continue

        doses = [e[1] for e in entries]
        max_dose = max(doses)
        min_dose = min(doses)

        if max_dose == 0:
            continue

        pct_diff = ((max_dose - min_dose) / max_dose) * 100
        if pct_diff > tolerance_pct:
            sources = [MedicationSource(e[0]) for e in entries]
            snap_ids = list({e[3] for e in entries})
            dose_summary = ", ".join(
                f"{e[1]}{e[2] or ''} ({e[0]})" for e in entries
            )
            detected.append(ConflictData(
                conflict_type=ConflictType.DOSE_MISMATCH,
                drug_names=[drug_name],
                sources_involved=sources,
                snapshot_ids=snap_ids,
                detail=f"'{drug_name}' has conflicting doses: {dose_summary}.",
            ))

    return detected


def detect_stopped_vs_active(
    snapshots: dict[str, dict],
) -> list[ConflictData]:
    """
    Detect cases where a drug is marked ACTIVE in one source and STOPPED in another.

    Logic:
      Build two sets per drug: sources_where_active, sources_where_stopped.
      If both sets are non-empty for the same drug, emit a STOPPED_VS_ACTIVE conflict.
    """
    # drug_name -> { "active": [(source, snap_id)], "stopped": [(source, snap_id)] }
    drug_status: dict[str, dict[str, list]] = {}

    for source, snap in snapshots.items():
        snap_id = snap.get("_id", "")
        for med in snap.get("medications", []):
            name = med.get("name_canonical", "")
            status = med.get("status", MedicationStatus.ACTIVE.value)
            if not name:
                continue
            entry = drug_status.setdefault(name, {"active": [], "stopped": []})
            if status == MedicationStatus.STOPPED.value:
                entry["stopped"].append((source, snap_id))
            else:
                entry["active"].append((source, snap_id))

    detected = []
    for drug_name, status_map in drug_status.items():
        if status_map["active"] and status_map["stopped"]:
            active_sources = [MedicationSource(e[0]) for e in status_map["active"]]
            stopped_sources = [MedicationSource(e[0]) for e in status_map["stopped"]]
            all_sources = active_sources + stopped_sources
            snap_ids = list({e[1] for e in status_map["active"] + status_map["stopped"]})

            active_str = ", ".join(e[0] for e in status_map["active"])
            stopped_str = ", ".join(e[0] for e in status_map["stopped"])
            detected.append(ConflictData(
                conflict_type=ConflictType.STOPPED_VS_ACTIVE,
                drug_names=[drug_name],
                sources_involved=all_sources,
                snapshot_ids=snap_ids,
                detail=(
                    f"'{drug_name}' is active in [{active_str}] "
                    f"but stopped in [{stopped_str}]."
                ),
            ))

    return detected


def detect_class_conflicts(
    snapshots: dict[str, dict],
    rules: dict,
) -> list[ConflictData]:
    """
    Detect blacklisted drug combinations present across any sources.

    Logic:
      Collect the set of all active canonical drug names across all snapshots.
      For each blacklisted pair in the rules file, check if both drugs are present.
      If so, emit a CLASS_CONFLICT.

    Note: we check across ALL sources combined — a dangerous combination is
    dangerous regardless of whether it came from the same source.
    """
    # All active drugs across all sources: name -> [(source, snap_id)]
    active_drugs: dict[str, list[tuple]] = {}

    for source, snap in snapshots.items():
        snap_id = snap.get("_id", "")
        for med in snap.get("medications", []):
            if med.get("status") == MedicationStatus.STOPPED.value:
                continue
            name = med.get("name_canonical", "")
            if name:
                active_drugs.setdefault(name, []).append((source, snap_id))

    detected = []
    for combo in rules.get("blacklisted_combinations", []):
        drug_a, drug_b = combo["drugs"][0], combo["drugs"][1]
        if drug_a in active_drugs and drug_b in active_drugs:
            sources_a = [MedicationSource(e[0]) for e in active_drugs[drug_a]]
            sources_b = [MedicationSource(e[0]) for e in active_drugs[drug_b]]
            all_sources = list({s for s in sources_a + sources_b})
            snap_ids = list({
                e[1] for e in active_drugs[drug_a] + active_drugs[drug_b]
            })
            detected.append(ConflictData(
                conflict_type=ConflictType.CLASS_CONFLICT,
                drug_names=[drug_a, drug_b],
                sources_involved=all_sources,
                snapshot_ids=snap_ids,
                detail=f"Blacklisted combination detected: '{drug_a}' + '{drug_b}'. {combo['reason']}",
            ))

    return detected


# ------------------------------------------------------------------ #
# DB layer
# ------------------------------------------------------------------ #

async def fetch_latest_snapshots(patient_id: str) -> dict[str, dict]:
    """
    Return the most recent snapshot per source for a patient.

    Returns { source_value: snapshot_doc } — at most 3 entries
    (one per MedicationSource enum value).
    """
    result: dict[str, dict] = {}
    for source in MedicationSource:
        doc = await medication_snapshots().find_one(
            {"patient_id": patient_id, "source": source.value},
            sort=[("version", -1)],
        )
        if doc is not None:
            result[source.value] = doc
    return result


async def _existing_open_conflict_key(patient_id: str) -> set[tuple]:
    """
    Return a set of (conflict_type, frozenset(drug_names)) for all
    currently unresolved conflicts for this patient.

    Used to avoid creating duplicate unresolved conflict records.
    """
    existing = set()
    cursor = conflicts().find(
        {"patient_id": patient_id, "status": ConflictStatus.UNRESOLVED.value},
        projection={"conflict_type": 1, "drug_names": 1},
    )
    async for doc in cursor:
        key = (doc["conflict_type"], frozenset(doc.get("drug_names", [])))
        existing.add(key)
    return existing


async def save_conflicts(
    patient_id: str,
    clinic_id: str,
    detected: list[ConflictData],
) -> list[Conflict]:
    """
    Persist new conflicts, skipping any that are already open (unresolved).

    Returns the list of newly created Conflict documents.
    """
    if not detected:
        return []

    existing_keys = await _existing_open_conflict_key(patient_id)
    new_conflicts = []

    for item in detected:
        key = (item.conflict_type.value, frozenset(item.drug_names))
        if key in existing_keys:
            continue  # already an open conflict for this drug+type

        conflict_id = str(ObjectId())
        conflict = Conflict(
            _id=conflict_id,
            patient_id=patient_id,
            clinic_id=clinic_id,
            conflict_type=item.conflict_type,
            status=ConflictStatus.UNRESOLVED,
            drug_names=item.drug_names,
            sources_involved=item.sources_involved,
            snapshot_ids=item.snapshot_ids,
            detail=item.detail,
            detected_at=datetime.now(timezone.utc),
        )
        doc = conflict.model_dump(by_alias=True, mode="json")
        await conflicts().insert_one(doc)
        new_conflicts.append(conflict)

    return new_conflicts


# ------------------------------------------------------------------ #
# Orchestrator — called by the ingestion route
# ------------------------------------------------------------------ #

async def run_conflict_detection(patient_id: str, clinic_id: str) -> list[Conflict]:
    """
    Full conflict detection pipeline for a patient.

    1. Fetch latest snapshot per source
    2. Run all three pure detectors
    3. Deduplicate against existing open conflicts
    4. Persist and return new conflicts

    Returns an empty list if no new conflicts are detected.
    """
    snapshots = await fetch_latest_snapshots(patient_id)

    if len(snapshots) < 2:
        # Need at least two sources to compare — nothing to detect yet
        return []

    tolerance = _RULES.get("dose_tolerance_percent", 0)

    all_detected: list[ConflictData] = []
    all_detected.extend(detect_dose_mismatches(snapshots, tolerance_pct=tolerance))
    all_detected.extend(detect_stopped_vs_active(snapshots))
    all_detected.extend(detect_class_conflicts(snapshots, _RULES))

    return await save_conflicts(patient_id, clinic_id, all_detected)