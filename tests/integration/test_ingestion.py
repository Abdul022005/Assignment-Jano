"""
Integration tests for the medication ingestion endpoint.

Uses httpx AsyncClient against the real FastAPI app.
MongoDB calls are patched so no real database is needed.

Tests cover:
  - Successful ingest: correct response shape and version numbering
  - Patient not found → 404
  - Invalid source enum → 422
  - Missing required fields → 422
  - Malformed medication item inside list → 422
  - Empty medications list → 201 (valid state)
  - Version increments on second ingest from same source
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def make_async_cursor(docs: list[dict]):
    """Return a mock async cursor that yields the given docs."""
    cursor = MagicMock()
    cursor.__aiter__ = AsyncMock(return_value=iter(docs))

    async def async_iter(self):
        for doc in docs:
            yield doc

    cursor.__aiter__ = lambda self: async_iter(cursor)
    return cursor


PATIENT_DOC = {
    "_id": "patient_001",
    "name": "Jane Doe",
    "clinic_id": "clinic_a",
    "date_of_birth": "1965-04-12",
}

VALID_INGEST_BODY = {
    "source": "clinic_emr",
    "medications": [
        {"name_canonical": "Lisinopril", "dose": 10.0, "unit": "MG", "status": "active"},
        {"name_canonical": "Furosemide", "dose": 40.0, "unit": "mg", "status": "active"},
    ],
}


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def mock_db():
    """Patch connect/disconnect/get_db and all collection accessors."""
    with patch("app.main.connect", new_callable=AsyncMock), \
         patch("app.main.disconnect", new_callable=AsyncMock), \
         patch("app.main.get_db", return_value=MagicMock()), \
         patch("app.db.indexes.ensure_indexes", new_callable=AsyncMock):
        yield


# ------------------------------------------------------------------ #
# POST /patients/{patient_id}/medications
# ------------------------------------------------------------------ #

class TestIngestMedications:

    @pytest.mark.asyncio
    async def test_successful_ingest_returns_201(self, mock_db):
        with patch("app.services.ingestion.patients") as mock_patients, \
             patch("app.services.ingestion.medication_snapshots") as mock_snapshots:

            mock_patients.return_value.find_one = AsyncMock(return_value=PATIENT_DOC)
            # No prior snapshot → version will be 1
            mock_snapshots.return_value.find_one = AsyncMock(return_value=None)
            mock_snapshots.return_value.insert_one = AsyncMock()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/patients/patient_001/medications", json=VALID_INGEST_BODY)

        assert response.status_code == 201
        data = response.json()
        assert data["patient_id"] == "patient_001"
        assert data["source"] == "clinic_emr"
        assert data["version"] == 1
        assert data["medication_count"] == 2
        assert "snapshot_id" in data
        assert "ingested_at" in data

    @pytest.mark.asyncio
    async def test_medications_are_normalized_before_storage(self, mock_db):
        """Names and units must be lowercased — verified via insert_one call args."""
        with patch("app.services.ingestion.patients") as mock_patients, \
             patch("app.services.ingestion.medication_snapshots") as mock_snapshots:

            mock_patients.return_value.find_one = AsyncMock(return_value=PATIENT_DOC)
            mock_snapshots.return_value.find_one = AsyncMock(return_value=None)
            insert_mock = AsyncMock()
            mock_snapshots.return_value.insert_one = insert_mock

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post("/patients/patient_001/medications", json=VALID_INGEST_BODY)

        # Inspect what was written to the DB
        inserted_doc = insert_mock.call_args[0][0]
        names = [m["name_canonical"] for m in inserted_doc["medications"]]
        units = [m["unit"] for m in inserted_doc["medications"]]
        assert names == ["lisinopril", "furosemide"]
        assert units == ["mg", "mg"]

    @pytest.mark.asyncio
    async def test_version_increments_on_second_ingest(self, mock_db):
        """If a prior snapshot exists for this source, version = prior + 1."""
        with patch("app.services.ingestion.patients") as mock_patients, \
             patch("app.services.ingestion.medication_snapshots") as mock_snapshots:

            mock_patients.return_value.find_one = AsyncMock(return_value=PATIENT_DOC)
            # Simulate existing version 2
            mock_snapshots.return_value.find_one = AsyncMock(return_value={"version": 2})
            mock_snapshots.return_value.insert_one = AsyncMock()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/patients/patient_001/medications", json=VALID_INGEST_BODY)

        assert response.json()["version"] == 3

    @pytest.mark.asyncio
    async def test_patient_not_found_returns_404(self, mock_db):
        with patch("app.services.ingestion.patients") as mock_patients, \
             patch("app.services.ingestion.medication_snapshots"):

            mock_patients.return_value.find_one = AsyncMock(return_value=None)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/patients/nonexistent/medications", json=VALID_INGEST_BODY)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_source_returns_422(self, mock_db):
        body = {**VALID_INGEST_BODY, "source": "made_up_source"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/patients/patient_001/medications", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_source_returns_422(self, mock_db):
        body = {"medications": VALID_INGEST_BODY["medications"]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/patients/patient_001/medications", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_medications_field_returns_422(self, mock_db):
        body = {"source": "clinic_emr"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/patients/patient_001/medications", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_malformed_medication_item_returns_422(self, mock_db):
        """An item missing name_canonical should fail validation."""
        body = {
            "source": "clinic_emr",
            "medications": [{"dose": 10.0, "unit": "mg"}],  # no name_canonical
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/patients/patient_001/medications", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_medications_list_accepted(self, mock_db):
        """A source reporting zero active medications is valid."""
        with patch("app.services.ingestion.patients") as mock_patients, \
             patch("app.services.ingestion.medication_snapshots") as mock_snapshots:

            mock_patients.return_value.find_one = AsyncMock(return_value=PATIENT_DOC)
            mock_snapshots.return_value.find_one = AsyncMock(return_value=None)
            mock_snapshots.return_value.insert_one = AsyncMock()

            body = {"source": "patient_reported", "medications": []}
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/patients/patient_001/medications", json=body)

        assert response.status_code == 201
        assert response.json()["medication_count"] == 0

    @pytest.mark.asyncio
    async def test_empty_request_body_returns_422(self, mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/patients/patient_001/medications", json={})
        assert response.status_code == 422


# ------------------------------------------------------------------ #
# GET /patients/{patient_id}/medications/history
# ------------------------------------------------------------------ #

class TestMedicationHistory:

    @pytest.mark.asyncio
    async def test_returns_snapshots_for_patient(self, mock_db):
        snapshot_docs = [
            {"_id": "snap_2", "patient_id": "patient_001", "source": "clinic_emr",
             "version": 2, "medications": [], "ingested_at": "2026-03-01T10:00:00"},
            {"_id": "snap_1", "patient_id": "patient_001", "source": "clinic_emr",
             "version": 1, "medications": [], "ingested_at": "2026-02-01T10:00:00"},
        ]

        async def mock_async_iter(*args, **kwargs):
            for doc in snapshot_docs:
                yield doc

        with patch("app.api.routes.patients.patients") as mock_patients, \
             patch("app.api.routes.patients.medication_snapshots") as mock_snapshots:

            mock_patients.return_value.find_one = AsyncMock(return_value=PATIENT_DOC)
            mock_cursor = MagicMock()
            mock_cursor.__aiter__ = mock_async_iter
            mock_snapshots.return_value.find.return_value = mock_cursor

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/patients/patient_001/medications/history")

        assert response.status_code == 200
        data = response.json()
        assert data["patient_id"] == "patient_001"
        assert data["total"] == 2
        assert data["snapshots"][0]["snapshot_id"] == "snap_2"

    @pytest.mark.asyncio
    async def test_history_patient_not_found_returns_404(self, mock_db):
        with patch("app.api.routes.patients.patients") as mock_patients:
            mock_patients.return_value.find_one = AsyncMock(return_value=None)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/patients/ghost/medications/history")
        assert response.status_code == 404