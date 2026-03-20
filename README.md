# Medication Reconciliation & Conflict Reporting Service

A backend service that ingests medication lists from multiple clinical sources, detects conflicts across those sources, and surfaces unresolved conflicts for clinician review and reporting.

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI |
| Database | MongoDB 7.0 (via Motor async driver) |
| Validation | Pydantic v2 |
| Testing | pytest + pytest-asyncio + httpx |
| Runtime | Docker (MongoDB) + local Python venv |

---

## Setup — clone → install → run in under 5 minutes

### Prerequisites
- Python 3.12+
- Docker & Docker Compose

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd med-reconciliation
```

### 2. Start MongoDB

```bash
docker-compose up -d
```

MongoDB will be available at `localhost:27017`.

### 3. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# .env is pre-filled with defaults matching docker-compose — no edits needed for local dev
```

### 5. Run the service

```bash
uvicorn app.main:app --reload
```

API available at: http://localhost:8000  
Interactive docs: http://localhost:8000/docs

### 6. Seed the database

```bash
python scripts/seed.py
```

Expected output:
```
Inserting 3 clinics...
Inserting 15 patients...
Inserting 32 snapshots...
Detecting conflicts...
--- Seed complete ---
  Clinics:   3
  Patients:  15
  Snapshots: 32
  Conflicts: ~14  (13 unresolved, 1 resolved)
```

The script is idempotent safe to run multiple times. It clears and re-seeds if existing data is found.

**What the seed data covers:**

| Clinic | Patients | Conflict types seeded |
|---|---|---|
| Kochi Dialysis Center | 5 | Dose mismatches (lisinopril, warfarin, metoprolol, furosemide) |
| Calicut Renal Institute | 5 | Stopped vs active (warfarin, metoprolol, lisinopril, furosemide, aspirin) |
| Trivandrum Kidney Clinic | 5 | Class conflicts (warfarin+aspirin, lisinopril+spironolactone, furosemide+gentamicin) + 2 clean patients |

Edge cases included: all-3-sources patient, version-2 snapshot, missing dose field, 1 pre-resolved conflict.

### 7. Run tests

```bash
pytest
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/clinics` | List all clinics |
| POST | `/clinics` | Create a clinic |
| GET | `/clinics/{clinic_id}` | Get a clinic by ID |
| GET | `/clinics/{clinic_id}/patients/unresolved-conflicts` | Patients with ≥1 unresolved conflict |
| GET | `/clinics/{clinic_id}/reports/conflicts-last-30-days` | Aggregate conflict counts (configurable window) |
| POST | `/patients` | Create a patient |
| GET | `/patients/{patient_id}` | Get a patient |
| POST | `/patients/{patient_id}/medications` | Ingest a medication list — triggers conflict detection |
| GET | `/patients/{patient_id}/medications/history` | View versioned snapshot history (filter by `?source=`) |
| GET | `/patients/{patient_id}/conflicts` | List conflicts (filter by `?status=unresolved`) |
| GET | `/conflicts/{conflict_id}` | Get a single conflict by ID |
| PATCH | `/conflicts/{conflict_id}/resolve` | Resolve a conflict with audit trail |


## Normalization rules

Applied at ingest time before any data is persisted:

| Field | Rule |
|---|---|
| `name_canonical` | Lowercase, strip leading/trailing whitespace |
| `unit` | Lowercase, strip whitespace, expand aliases (`MG`→`mg`, `ug`/`µg`→`mcg`, `u`→`units`, etc.) |
| `frequency` | Lowercase, strip whitespace (free text, not further parsed) |
| `notes` | Strip whitespace only |

Unknown units are passed through lowercased rather than rejected this avoids
breaking ingest for uncommon units while still normalizing the common ones.

---
## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        FastAPI App                          │
│                                                             │
│  POST /patients/{id}/medications                            │
│           │                                                 │
│           ▼                                                 │
│   ┌──────────────────┐                                      │
│   │  Normalization   │  lowercase names, canonicalize units │
│   └────────┬─────────┘                                      │
│            │                                                │
│            ▼                                                │
│   ┌──────────────────┐                                      │
│   │  MedicationSnapshot  │  append-only, versioned per source│
│   │  (MongoDB)       │                                      │
│   └────────┬─────────┘                                      │
│            │                                                │
│            ▼                                                │
│   ┌──────────────────────────────────────┐                  │
│   │       Conflict Detection Engine      │                  │
│   │  ┌────────────┐ ┌─────────────────┐  │                  │
│   │  │Dose Mismatch│ │Stopped vs Active│  │                  │
│   │  └────────────┘ └─────────────────┘  │                  │
│   │       ┌──────────────────┐           │                  │
│   │       │  Class Conflict  │           │                  │
│   │       └──────────────────┘           │                  │
│   └────────────────┬─────────────────────┘                  │
│                    │                                        │
│                    ▼                                        │
│           ┌────────────────┐                                │
│           │  Conflict docs │  status: unresolved            │
│           │  (MongoDB)     │                                │
│           └───────┬────────┘                                │
│                   │                                         │
│         ┌─────────┴──────────┐                              │
│         ▼                    ▼                              │
│  ┌─────────────┐    ┌──────────────────┐                    │
│  │  Resolution │    │    Reporting     │                    │
│  │  Endpoints  │    │  (Aggregation    │                    │
│  │  PATCH      │    │   Pipelines)     │                    │
│  └─────────────┘    └──────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

### Directory structure

```
med-reconciliation/
├── app/
│   ├── api/
│   │   └── routes/          # One file per resource (patients, conflicts, clinics)
│   ├── core/
│   │   └── config.py        # Pydantic settings loaded from .env
│   ├── db/
│   │   └── client.py        # Motor async client, connect/disconnect lifecycle
│   ├── models/              # Pydantic document models (Patient, Snapshot, Conflict)
│   ├── services/            # Business logic (ingestion, conflict detection, reporting)
│   └── main.py              # FastAPI app with lifespan
├── tests/
│   ├── unit/                # Pure function tests (conflict detection, normalization)
│   └── integration/         # Endpoint tests with test database
├── scripts/
│   └── seed.py              # Synthetic dataset generator
├── docs/                    # Supplementary diagrams / schema description
├── docker-compose.yml
├── requirements.txt
├── pytest.ini
└── .env.example
```

---

## MongoDB Schema

Full schema description with indexing rationale: [docs/schema.md](docs/schema.md)

**Collections at a glance**

| Collection | Purpose | Key indexes |
|---|---|---|
| `patients` | One document per patient | `clinic_id` |
| `medication_snapshots` | Immutable per-source snapshots (append-only) | `(patient_id, source, version)` unique, `(patient_id, ingested_at)`, `(clinic_id, ingested_at)` |
| `conflicts` | Detected disagreements with resolution audit trail | `(patient_id, status)`, `(clinic_id, status, detected_at)` |

---

## Assumptions & Trade-offs

### Resolution model

Conflicts are resolved via `PATCH /conflicts/{id}/resolve`. The request requires:

| Field | Required | Notes |
|---|---|---|
| `reason` | Yes | Free-text clinical rationale always required for audit |
| `resolved_by` | Yes | Clinician identifier |
| `chosen_source` | No | Which source was accepted. Nullable a clinician may resolve without accepting either source |

Re-resolving an already-resolved conflict returns `409 Conflict`. Overwriting a clinical decision silently would be unsafe.

The resolution is stored as an embedded sub-document on the Conflict record never in a separate collection because it only ever belongs to one conflict and is always fetched together with it.

### Versioning strategy
Every ingest from any source creates a **new, immutable MedicationSnapshot** rather than updating an existing one. This means:
- History is fully preserved you can reconstruct the medication list at any point in time.
- The trade-off is document proliferation for high-frequency ingest sources, which we accept given the relatively low volume of dialysis patient records.

### Truth source / conflict resolution
There is **no designated authoritative source**. Instead, a conflict is resolved by a clinician who explicitly records:
- `chosen_source` — which source's version they accepted
- `resolution_reason` — free-text rationale
- `resolved_by` — identifier of the clinician
- `resolved_at` — timestamp

This is a deliberate design choice: in a clinical setting, overriding a medication record without a human decision and an audit trail would be unsafe.

### Normalization scope
Normalization at ingest time covers: lowercasing drug names, trimming whitespace, and standardizing common dose unit abbreviations (e.g. `MG` → `mg`, `MCG` → `mcg`). Brand name / generic name aliasing is out of scope — it would require a drug database.

### Conflict rules
A static `conflict_rules.json` file defines:
- Per-drug safe dose ranges
- Blacklisted drug-class combination pairs

This is explicitly acknowledged as a simplification. A production system would integrate a live formulary or drug interaction API (e.g. RxNorm, DrFirst).

### Denormalization
`clinic_id` is stored directly on both `Patient` and `MedicationSnapshot` documents to make the reporting aggregation pipelines efficient without requiring `$lookup` joins. The trade-off is that if a patient transfers clinics, both documents need updating — acceptable given the low likelihood of this event.

---

## Reporting endpoints

Both reporting endpoints use MongoDB aggregation pipelines — no Python-side filtering.

### `GET /clinics/{id}/patients/unresolved-conflicts`
Pipeline stages: `$match` (clinic + unresolved) → `$group` (count per patient) → `$match` (threshold) → `$lookup` (patient name) → `$project` → `$sort`

Query params:
- `min_conflicts` (default `1`, min `1`) — minimum unresolved conflicts to include a patient

### `GET /clinics/{id}/reports/conflicts-last-30-days`
Pipeline stages: `$match` (clinic + date window) → `$group` (total/unresolved/resolved counts per patient) → `$match` (threshold) → `$lookup` (patient name) → `$project` → `$sort`

Query params:
- `days` (default `30`, range `1–365`) — lookback window
- `min_conflicts` (default `2`, min `1`) — minimum conflicts to include a patient

The endpoint name uses `30-days` per the assignment spec. The window is configurable — the default matches the spec exactly.

Both pipelines use the `(clinic_id, status, detected_at)` compound index defined in Stage 2.

---

## Known Limitations & What I Would Do Next

- **No authentication / authorization** — endpoints are open. Next step: add OAuth2 or API key middleware.
- **No pagination** on list endpoints — fine for the dataset size here; production would need cursor-based pagination.
- **Brand/generic aliasing** not handled — two records for "Lisinopril" and "Zestril" would not be detected as the same drug.
- **Conflict rules are static** — a real system would pull from a live drug interaction service.
- **No background job for re-evaluation** — if a conflict rule changes, existing unresolved conflicts are not re-evaluated. A Celery/ARQ worker queue would handle this.

---

## AI Usage

### What I used AI for
- Boilerplate scaffolding: project folder structure, `docker-compose.yml`, `pytest.ini`, `.gitignore`
- Initial Pydantic model shapes and FastAPI route skeletons
- MongoDB aggregation pipeline structure for the reporting endpoints
- Test file structure and initial test cases

### What I reviewed and changed manually
- All conflict detection logic reviewed each detector carefully against the assignment spec to ensure `stopped_vs_active` correctly handles the case where a drug appears in only one source (should not flag)
- The deduplication logic in `save_conflicts()` the AI's first version would have created duplicate conflict records on every re-ingest; I rewrote it to check existing open conflicts by `(conflict_type, drug_names)` key
- The seed script rewrote to use direct detection function calls rather than the HTTP layer, which is faster and avoids a circular dependency on the running server
- `conflict_rules.json` selected clinically relevant dialysis drug combinations rather than generic examples
- All README trade-off sections written manually to reflect actual decisions made during development

### One example where I disagreed with the AI's output
The AI initially put conflict detection logic inside `ingestion.py`, calling it at the end of `ingest_medications()`. I separated it into its own service (`conflict_detection.py`) called from the route handler instead. The reason: keeping ingestion and detection in separate services means you can test each in complete isolation, and it makes it straightforward to add a background job that re-runs detection when rules change without touching ingestion at all.

---

## Demo

After seeding, the key flows to verify manually:

**1. Check unresolved conflicts for Kochi Dialysis Center**
```
GET /clinics/{kochi_id}/patients/unresolved-conflicts
```
Expected: 5 patients, each with dose mismatch conflicts.

**2. Check the 30-day report for Calicut Renal Institute**
```
GET /clinics/{calicut_id}/reports/conflicts-last-30-days?min_conflicts=1
```
Expected: patients with stopped-vs-active conflicts detected in the last 30 days.

**3. Ingest a new medication list and watch conflict detection fire**
```
POST /patients/{patient_id}/medications
{ "source": "patient_reported", "medications": [{"name_canonical": "Warfarin", "dose": 9.0, "unit": "MG"}] }
```
Response includes `conflicts_detected` count and `conflict_ids`.

**4. Resolve a conflict**
```
PATCH /conflicts/{conflict_id}/resolve
{ "chosen_source": "clinic_emr", "reason": "Confirmed with physician", "resolved_by": "dr_smith" }
```

**5. View full medication history for a patient**
```
GET /patients/{patient_id}/medications/history
```
Expected: multiple snapshots with ascending version numbers.

Interactive API docs available at **http://localhost:8000/docs**