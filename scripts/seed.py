"""
Seed script — generates a synthetic dataset of 15 patients across 3 clinics.

Designed to exercise every conflict type:
  - dose_mismatch       (patients 1–5, Clinic A)
  - stopped_vs_active   (patients 6–10, Clinic B)
  - class_conflict      (patients 11–13, Clinic C)
  - no conflicts        (patients 14–15, clean baseline)

Edge cases included:
  - Patient with all 3 sources providing data
  - Patient with a second ingest (version=2) on one source
  - Patient with missing dose field in one source
  - One pre-resolved conflict to demonstrate the full lifecycle

Usage:
  python scripts/seed.py

Prerequisites:
  docker-compose up -d   (MongoDB must be running)
  cp .env.example .env

Idempotent: checks for existing clinics by name. If found, clears and re-seeds.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings
from app.services.conflict_detection import (
    detect_dose_mismatches,
    detect_stopped_vs_active,
    detect_class_conflicts,
    load_rules,
)
from app.models.common import ConflictStatus


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def oid() -> str:
    return str(ObjectId())


def med(name: str, dose: float | None = None, unit: str = "mg",
        freq: str = "once daily", status: str = "active") -> dict:
    return {
        "name_canonical": name,
        "dose": dose,
        "unit": unit,
        "frequency": freq,
        "status": status,
        "notes": None,
    }


def ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def snap(snap_id: str, patient_id: str, clinic_id: str, source: str,
         version: int, medications: list[dict], ingested_at: datetime) -> dict:
    return {
        "_id": snap_id,
        "patient_id": patient_id,
        "clinic_id": clinic_id,
        "source": source,
        "version": version,
        "medications": medications,
        "ingested_at": ingested_at,
    }


# ------------------------------------------------------------------ #
# Dataset definitions
# ------------------------------------------------------------------ #

CLINICS = [
    {"_id": oid(), "name": "Kochi Dialysis Center",    "location": "Kochi, Kerala"},
    {"_id": oid(), "name": "Calicut Renal Institute",   "location": "Calicut, Kerala"},
    {"_id": oid(), "name": "Trivandrum Kidney Clinic",  "location": "Trivandrum, Kerala"},
]


def make_dataset(ca: str, cb: str, cc: str) -> tuple[list[dict], list[dict]]:
    """Return (patients, snapshots) with clinic IDs wired in."""

    patients = [
        # Clinic A — dose mismatch scenarios
        {"_id": oid(), "name": "Arjun Nair",    "clinic_id": ca, "date_of_birth": "1958-03-14"},
        {"_id": oid(), "name": "Meera Pillai",   "clinic_id": ca, "date_of_birth": "1962-07-22"},
        {"_id": oid(), "name": "Suresh Menon",   "clinic_id": ca, "date_of_birth": "1949-11-05"},
        {"_id": oid(), "name": "Lakshmi Varma",  "clinic_id": ca, "date_of_birth": "1971-02-18"},
        {"_id": oid(), "name": "Rajan Thomas",   "clinic_id": ca, "date_of_birth": "1955-09-30"},
        # Clinic B — stopped vs active scenarios
        {"_id": oid(), "name": "Priya Krishnan", "clinic_id": cb, "date_of_birth": "1967-05-12"},
        {"_id": oid(), "name": "Anil Kumar",     "clinic_id": cb, "date_of_birth": "1960-08-25"},
        {"_id": oid(), "name": "Divya Raj",      "clinic_id": cb, "date_of_birth": "1975-01-08"},
        {"_id": oid(), "name": "Vijay Shankar",  "clinic_id": cb, "date_of_birth": "1953-12-19"},
        {"_id": oid(), "name": "Sreeja Mohan",   "clinic_id": cb, "date_of_birth": "1964-04-03"},
        # Clinic C — class conflicts + clean patients
        {"_id": oid(), "name": "Gopinath Iyer",  "clinic_id": cc, "date_of_birth": "1950-06-27"},
        {"_id": oid(), "name": "Anitha Nambiar", "clinic_id": cc, "date_of_birth": "1969-10-14"},
        {"_id": oid(), "name": "Babu Cherian",   "clinic_id": cc, "date_of_birth": "1957-03-31"},
        {"_id": oid(), "name": "Sindhu George",  "clinic_id": cc, "date_of_birth": "1973-08-07"},
        {"_id": oid(), "name": "Mathew Jose",    "clinic_id": cc, "date_of_birth": "1961-11-22"},
    ]

    p = [pat["_id"] for pat in patients]

    snapshots = [
        # P0 Arjun — dose mismatch: lisinopril 10mg vs 20mg
        snap(oid(), p[0], ca, "clinic_emr", 1,
             [med("lisinopril", 10.0), med("furosemide", 40.0), med("amlodipine", 5.0)], ago(20)),
        snap(oid(), p[0], ca, "hospital_discharge", 1,
             [med("lisinopril", 20.0), med("furosemide", 40.0), med("amlodipine", 5.0)], ago(15)),

        # P1 Meera — dose mismatch: warfarin 2mg vs 5mg + class conflict (warfarin+aspirin)
        snap(oid(), p[1], ca, "clinic_emr", 1,
             [med("warfarin", 2.0), med("aspirin", 100.0), med("atorvastatin", 20.0)], ago(25)),
        snap(oid(), p[1], ca, "hospital_discharge", 1,
             [med("warfarin", 5.0), med("aspirin", 100.0)], ago(18)),
        snap(oid(), p[1], ca, "patient_reported", 1,
             [med("warfarin", 2.0), med("atorvastatin", 20.0)], ago(10)),

        # P2 Suresh — dose mismatch: metoprolol 25mg vs 100mg
        snap(oid(), p[2], ca, "clinic_emr", 1,
             [med("metoprolol", 25.0), med("sevelamer", 800.0)], ago(30)),
        snap(oid(), p[2], ca, "hospital_discharge", 1,
             [med("metoprolol", 100.0), med("sevelamer", 800.0)], ago(28)),

        # P3 Lakshmi — dose mismatch: furosemide 20mg vs 80mg
        snap(oid(), p[3], ca, "clinic_emr", 1,
             [med("furosemide", 20.0), med("lisinopril", 5.0)], ago(12)),
        snap(oid(), p[3], ca, "patient_reported", 1,
             [med("furosemide", 80.0), med("lisinopril", 5.0)], ago(8)),

        # P4 Rajan — edge case: missing dose in one source
        snap(oid(), p[4], ca, "clinic_emr", 1,
             [med("cinacalcet", 30.0), med("epoetin alfa", 100.0, unit="units")], ago(35)),
        snap(oid(), p[4], ca, "hospital_discharge", 1,
             [med("cinacalcet", None), med("epoetin alfa", 100.0, unit="units")], ago(33)),

        # P5 Priya — stopped vs active: warfarin active in EMR, stopped in discharge
        snap(oid(), p[5], cb, "clinic_emr", 1,
             [med("warfarin", 3.0, status="active"), med("furosemide", 40.0)], ago(22)),
        snap(oid(), p[5], cb, "hospital_discharge", 1,
             [med("warfarin", 3.0, status="stopped"), med("furosemide", 40.0)], ago(20)),

        # P6 Anil — stopped vs active + version 2 on clinic_emr
        snap(oid(), p[6], cb, "clinic_emr", 1,
             [med("metoprolol", 50.0, status="active"), med("amlodipine", 5.0)], ago(40)),
        snap(oid(), p[6], cb, "clinic_emr", 2,
             [med("metoprolol", 50.0, status="stopped"), med("amlodipine", 5.0)], ago(10)),
        snap(oid(), p[6], cb, "hospital_discharge", 1,
             [med("metoprolol", 50.0, status="active"), med("amlodipine", 5.0)], ago(38)),

        # P7 Divya — stopped vs active: lisinopril
        snap(oid(), p[7], cb, "clinic_emr", 1,
             [med("lisinopril", 10.0, status="stopped"), med("atorvastatin", 40.0)], ago(17)),
        snap(oid(), p[7], cb, "patient_reported", 1,
             [med("lisinopril", 10.0, status="active"), med("atorvastatin", 40.0)], ago(14)),

        # P8 Vijay — stopped vs active + dose mismatch (multiple conflicts)
        snap(oid(), p[8], cb, "clinic_emr", 1,
             [med("furosemide", 40.0, status="active"), med("warfarin", 4.0)], ago(19)),
        snap(oid(), p[8], cb, "hospital_discharge", 1,
             [med("furosemide", 80.0, status="stopped"), med("warfarin", 4.0)], ago(16)),

        # P9 Sreeja — stopped vs active: aspirin
        snap(oid(), p[9], cb, "clinic_emr", 1,
             [med("aspirin", 100.0, status="active"), med("metoprolol", 50.0)], ago(11)),
        snap(oid(), p[9], cb, "hospital_discharge", 1,
             [med("aspirin", 100.0, status="stopped"), med("metoprolol", 50.0)], ago(9)),

        # P10 Gopinath — class conflict: warfarin + aspirin
        snap(oid(), p[10], cc, "clinic_emr", 1,
             [med("warfarin", 3.0), med("aspirin", 75.0), med("furosemide", 40.0)], ago(14)),
        snap(oid(), p[10], cc, "hospital_discharge", 1,
             [med("warfarin", 3.0), med("furosemide", 40.0)], ago(12)),

        # P11 Anitha — class conflict: lisinopril + spironolactone
        snap(oid(), p[11], cc, "clinic_emr", 1,
             [med("lisinopril", 10.0), med("spironolactone", 25.0), med("atorvastatin", 20.0)], ago(21)),
        snap(oid(), p[11], cc, "patient_reported", 1,
             [med("lisinopril", 10.0), med("spironolactone", 25.0)], ago(18)),

        # P12 Babu — class conflict: furosemide + gentamicin
        snap(oid(), p[12], cc, "clinic_emr", 1,
             [med("furosemide", 80.0), med("gentamicin", 80.0)], ago(7)),
        snap(oid(), p[12], cc, "hospital_discharge", 1,
             [med("furosemide", 80.0), med("gentamicin", 80.0)], ago(5)),

        # P13 Sindhu — clean, no conflicts
        snap(oid(), p[13], cc, "clinic_emr", 1,
             [med("amlodipine", 5.0), med("atorvastatin", 20.0), med("levothyroxine", 0.05)], ago(16)),
        snap(oid(), p[13], cc, "hospital_discharge", 1,
             [med("amlodipine", 5.0), med("atorvastatin", 20.0), med("levothyroxine", 0.05)], ago(14)),

        # P14 Mathew — single source, no conflicts possible
        snap(oid(), p[14], cc, "clinic_emr", 1,
             [med("metoprolol", 50.0), med("furosemide", 40.0)], ago(6)),
    ]

    return patients, snapshots


# ------------------------------------------------------------------ #
# Conflict detection (direct, no HTTP)
# ------------------------------------------------------------------ #

RULES = load_rules()


def detect_conflicts_for_patient(
    patient_id: str, clinic_id: str,
    all_snapshots: list[dict],
) -> list[dict]:
    """
    Run all three detectors against the latest snapshot per source
    for a given patient. Returns a list of conflict dicts ready to insert.
    """
    # Group snapshots by source, pick the latest version per source
    latest: dict[str, dict] = {}
    for s in all_snapshots:
        if s["patient_id"] != patient_id:
            continue
        src = s["source"]
        if src not in latest or s["version"] > latest[src]["version"]:
            latest[src] = s

    if len(latest) < 2:
        return []

    tolerance = RULES.get("dose_tolerance_percent", 0)
    detected = (
        detect_dose_mismatches(latest, tolerance_pct=tolerance)
        + detect_stopped_vs_active(latest)
        + detect_class_conflicts(latest, RULES)
    )

    conflicts = []
    seen: set[tuple] = set()
    for item in detected:
        key = (item.conflict_type.value, frozenset(item.drug_names))
        if key in seen:
            continue
        seen.add(key)
        conflicts.append({
            "_id": oid(),
            "patient_id": patient_id,
            "clinic_id": clinic_id,
            "conflict_type": item.conflict_type.value,
            "status": "unresolved",
            "drug_names": item.drug_names,
            "sources_involved": [s.value for s in item.sources_involved],
            "snapshot_ids": item.snapshot_ids,
            "detail": item.detail,
            "detected_at": datetime.now(timezone.utc),
            "resolution": None,
        })
    return conflicts


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

async def seed():
    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_db_name]

    seed_names = [c["name"] for c in CLINICS]
    existing = await db.clinics.find_one({"name": {"$in": seed_names}})
    if existing:
        print("Existing seed data found — clearing and re-seeding...")
        ids = [d["_id"] async for d in db.clinics.find({"name": {"$in": seed_names}}, {"_id": 1})]
        await db.patients.delete_many({"clinic_id": {"$in": ids}})
        await db.medication_snapshots.delete_many({"clinic_id": {"$in": ids}})
        await db.conflicts.delete_many({"clinic_id": {"$in": ids}})
        await db.clinics.delete_many({"_id": {"$in": ids}})
        print("Cleared.")

    print(f"Inserting {len(CLINICS)} clinics...")
    await db.clinics.insert_many(CLINICS)

    ca, cb, cc = CLINICS[0]["_id"], CLINICS[1]["_id"], CLINICS[2]["_id"]
    patients, snapshots = make_dataset(ca, cb, cc)

    print(f"Inserting {len(patients)} patients...")
    await db.patients.insert_many(patients)

    print(f"Inserting {len(snapshots)} snapshots...")
    await db.medication_snapshots.insert_many(snapshots)

    # Run conflict detection for every patient
    print("Detecting conflicts...")
    all_conflicts = []
    for pat in patients:
        detected = detect_conflicts_for_patient(pat["_id"], pat["clinic_id"], snapshots)
        all_conflicts.extend(detected)

    if all_conflicts:
        await db.conflicts.insert_many(all_conflicts)

    # Add one pre-resolved conflict on P0 to show the full lifecycle
    p0 = patients[0]
    p0_snaps = [s["_id"] for s in snapshots if s["patient_id"] == p0["_id"]]
    resolved = {
        "_id": oid(),
        "patient_id": p0["_id"],
        "clinic_id": p0["clinic_id"],
        "conflict_type": "dose_mismatch",
        "status": "resolved",
        "drug_names": ["amlodipine"],
        "sources_involved": ["clinic_emr", "hospital_discharge"],
        "snapshot_ids": p0_snaps,
        "detail": "amlodipine: 5.0mg (clinic_emr) vs 10.0mg (hospital_discharge) — pre-resolved.",
        "detected_at": ago(20),
        "resolution": {
            "chosen_source": "clinic_emr",
            "reason": "Confirmed with prescribing cardiologist. 5mg is the correct maintenance dose.",
            "resolved_by": "dr_anand_kumar",
            "resolved_at": ago(18),
        },
    }
    await db.conflicts.insert_one(resolved)

    # Print summary
    pc = await db.patients.count_documents({})
    sc = await db.medication_snapshots.count_documents({})
    cc_total = await db.conflicts.count_documents({})
    cc_unres = await db.conflicts.count_documents({"status": "unresolved"})
    cc_res   = await db.conflicts.count_documents({"status": "resolved"})

    print("\n--- Seed complete ---")
    print(f"  Clinics:   {len(CLINICS)}")
    print(f"  Patients:  {pc}")
    print(f"  Snapshots: {sc}")
    print(f"  Conflicts: {cc_total}  ({cc_unres} unresolved, {cc_res} resolved)")
    print("\nService ready: uvicorn app.main:app --reload")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())