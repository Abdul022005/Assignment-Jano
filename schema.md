# MongoDB Schema

Three collections. Each is described below with its document shape,
field rationale, and index strategy.

---

## Collection: `patients`

```
{
  "_id":            ObjectId,       -- MongoDB default primary key
  "name":           string,         -- patient full name
  "clinic_id":      string,         -- which clinic this patient belongs to
  "date_of_birth":  string,         -- ISO date "YYYY-MM-DD"
  "created_at":     ISODate,
  "updated_at":     ISODate
}
```

**Why so slim?**
Full demographics (address, insurance, contact) belong in the EMR.
This service only needs what's required to link snapshots to a clinic
and to identify the patient in conflict reports.

**Indexes**

| Index | Fields | Reason |
|---|---|---|
| `clinic_id_1` | `clinic_id` ASC | Primary reporting dimension — every aggregation filters or groups by clinic |

---

## Collection: `medication_snapshots`

```
{
  "_id":          ObjectId,
  "patient_id":   string,           -- references patients._id
  "clinic_id":    string,           -- denormalised from patient for fast aggregation
  "source":       enum,             -- "clinic_emr" | "hospital_discharge" | "patient_reported"
  "version":      integer,          -- 1-based, scoped to (patient_id, source)
  "ingested_at":  ISODate,
  "medications": [
    {
      "name_canonical": string,     -- lowercase, trimmed drug name
      "dose":           float,      -- numeric dose value
      "unit":           string,     -- normalised unit e.g. "mg", "mcg"
      "frequency":      string,     -- free text e.g. "twice daily"
      "status":         enum,       -- "active" | "stopped"
      "notes":          string
    }
  ]
}
```

**Versioning decision — append-only**

Every ingest creates a new document with `version = previous_max + 1`.
Existing snapshots are never mutated. This means:

- Full history is preserved with no extra audit table needed.
- You can reconstruct exactly what any source reported at any past time.
- Conflict detection always compares the *latest* snapshot per source.

The cost is document growth over time. For a dialysis patient seen
3× per week across 3 sources, that's ~9 documents/week — manageable.

**Why `clinic_id` is denormalised here**

The 30-day reporting aggregation groups by clinic across snapshots.
Storing `clinic_id` directly avoids a `$lookup` to `patients`,
which would be slow at scale. The trade-off: if a patient transfers
clinics, both `patients` and their snapshots need updating.
This is rare enough that we accept it.

**Indexes**

| Index | Fields | Reason |
|---|---|---|
| `patient_source_version_unique` | `(patient_id, source, version)` UNIQUE | Enforces versioning invariant; supports "latest version for this source" query |
| `patient_history` | `(patient_id, ingested_at DESC)` | History endpoint — all snapshots for a patient, newest first |
| `clinic_recent_snapshots` | `(clinic_id, ingested_at DESC)` | 30-day report date-range filter scoped to clinic |

---

## Collection: `conflicts`

```
{
  "_id":              ObjectId,
  "patient_id":       string,
  "clinic_id":        string,         -- denormalised for aggregation
  "conflict_type":    enum,           -- "dose_mismatch" | "stopped_vs_active" | "class_conflict"
  "status":           enum,           -- "unresolved" | "resolved"
  "drug_names":       [string],       -- canonical names of involved drugs
  "sources_involved": [enum],         -- which sources disagree
  "snapshot_ids":     [string],       -- references to the snapshots being compared
  "detail":           string,         -- human-readable description for clinicians
  "detected_at":      ISODate,

  "resolution": {                     -- null until resolved
    "chosen_source":  enum | null,    -- which source's version was accepted
    "reason":         string,         -- clinician's free-text rationale
    "resolved_by":    string,         -- clinician identifier
    "resolved_at":    ISODate
  }
}
```

**Resolution model decision**

There is no automatic or rule-based resolution.
A conflict is resolved only when a clinician explicitly calls
`PATCH /conflicts/{id}/resolve` with a reason and their identifier.
This is intentional — overriding a medication record without a human
decision and an audit trail is unsafe in a clinical context.

`chosen_source` is nullable: a clinician may resolve a conflict as
"discussed with patient — neither source was accurate, prescription updated"
without choosing either source. In that case `reason` carries the full
explanation.

**`snapshot_ids` as references (not embedded)**

Snapshots are not embedded inside the conflict document because:
1. A snapshot can be involved in multiple conflicts.
2. Snapshots can be large (many medications).
3. We want to fetch snapshot detail on demand, not always.

**Indexes**

| Index | Fields | Reason |
|---|---|---|
| `patient_conflict_status` | `(patient_id, status)` | Most common lookup: "unresolved conflicts for this patient" |
| `clinic_conflict_status_date` | `(clinic_id, status, detected_at DESC)` | Both reporting endpoints: clinic filter + status filter + date window |

---

## Index strategy summary

All reporting queries are served by compound indexes —
no reporting endpoint requires a full collection scan.
The `clinic_id` field appears in indexes on all three collections
because every aggregate report is scoped to a clinic.