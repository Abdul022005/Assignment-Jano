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

### 6. Seed the database (after Stage 7)

```bash
python scripts/seed.py
```

### 7. Run tests

```bash
pytest
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/patients/{patient_id}/medications` | Ingest a medication list from a source |
| GET | `/patients/{patient_id}/medications/history` | View versioned snapshot history |
| GET | `/patients/{patient_id}/conflicts` | List conflicts with optional status filter |
| PATCH | `/conflicts/{conflict_id}/resolve` | Mark a conflict as resolved |
| GET | `/clinics/{clinic_id}/patients/unresolved-conflicts` | Patients with ≥1 unresolved conflict |
| GET | `/clinics/{clinic_id}/reports/conflicts-last-30-days` | Aggregate conflict counts (configurable window) |

---

## Architecture Overview

```
POST /patients/{id}/medications
         │
         ▼
  Normalization Layer
  (lowercase names, strip units)
         │
         ▼
  MedicationSnapshot stored
  (append-only versioning)
         │
         ▼
  Conflict Detection Engine
  (dose mismatch / stopped drug / class conflict)
         │
         ▼
  Conflict records stored
  (status: unresolved)
         │
    ┌────┴────┐
    ▼         ▼
Clinician   Reporting
Resolution  Aggregation
Endpoints   Endpoints
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

> Full schema description with indexing rationale: see [docs/schema.md](docs/schema.md) *(added in Stage 2)*

---

## Assumptions & Trade-offs

### Versioning strategy
Every ingest from any source creates a **new, immutable MedicationSnapshot** rather than updating an existing one. This means:
- History is fully preserved — you can reconstruct the medication list at any point in time.
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

## Known Limitations & What I Would Do Next

- **No authentication / authorization** — endpoints are open. Next step: add OAuth2 or API key middleware.
- **No pagination** on list endpoints — fine for the dataset size here; production would need cursor-based pagination.
- **Brand/generic aliasing** not handled, two records for "Lisinopril" and "Zestril" would not be detected as the same drug.
- **Conflict rules are static** — a real system would pull from a live drug interaction service.
- **No background job for re-evaluation** — if a conflict rule changes, existing unresolved conflicts are not re-evaluated. A Celery/ARQ worker queue would handle this.

---

## AI Usage

TODO

- **What I used AI for:**
- **What I reviewed and changed manually:**
- **One example where I disagreed with the AI's output and why:**

---

## Demo

TODO