"""
Integration tests for conflict resolution.

Covers:
  - Successful resolution with chosen_source
  - Successful resolution without chosen_source (neither source correct)
  - Resolving an already-resolved conflict → 409
  - Conflict not found → 404
  - Missing required fields (reason, resolved_by) → 422
  - Response shape includes full resolution sub-document
  - GET /conflicts/{id} returns correct document
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app

# ------------------------------------------------------------------ #
# Shared fixtures and helpers
# ------------------------------------------------------------------ #

UNRESOLVED_CONFLICT = {
    "_id": "conflict_001",
    "patient_id": "patient_001",
    "clinic_id": "clinic_a",
    "conflict_type": "dose_mismatch",
    "status": "unresolved",
    "drug_names": ["lisinopril"],
    "sources_involved": ["clinic_emr", "hospital_discharge"],
    "snapshot_ids": ["snap_1", "snap_2"],
    "detail": "lisinopril: 10mg in clinic_emr vs 20mg in hospital_discharge",
    "detected_at": "2026-03-01T10:00:00",
    "resolution": None,
}

RESOLVED_CONFLICT = {
    **UNRESOLVED_CONFLICT,
    "status": "resolved",
    "resolution": {
        "chosen_source": "clinic_emr",
        "reason": "Confirmed with prescribing physician.",
        "resolved_by": "dr_smith",
        "resolved_at": "2026-03-10T12:00:00",
    },
}

VALID_RESOLVE_BODY = {
    "chosen_source": "clinic_emr",
    "reason": "Confirmed with prescribing physician.",
    "resolved_by": "dr_smith",
}


@pytest.fixture
def mock_db():
    with patch("app.main.connect", new_callable=AsyncMock), \
         patch("app.main.disconnect", new_callable=AsyncMock), \
         patch("app.main.get_db", return_value=MagicMock()), \
         patch("app.db.indexes.ensure_indexes", new_callable=AsyncMock):
        yield


# ------------------------------------------------------------------ #
# GET /conflicts/{conflict_id}
# ------------------------------------------------------------------ #

class TestGetConflict:

    @pytest.mark.asyncio
    async def test_returns_conflict_by_id(self, mock_db):
        with patch("app.api.routes.conflicts.conflicts") as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=dict(UNRESOLVED_CONFLICT))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/conflicts/conflict_001")

        assert response.status_code == 200
        data = response.json()
        assert data["conflict_id"] == "conflict_001"
        assert data["status"] == "unresolved"
        assert data["drug_names"] == ["lisinopril"]

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, mock_db):
        with patch("app.api.routes.conflicts.conflicts") as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=None)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/conflicts/nonexistent")

        assert response.status_code == 404


# ------------------------------------------------------------------ #
# PATCH /conflicts/{conflict_id}/resolve
# ------------------------------------------------------------------ #

class TestResolveConflict:

    @pytest.mark.asyncio
    async def test_successful_resolution_with_chosen_source(self, mock_db):
        updated_doc = dict(RESOLVED_CONFLICT)
        updated_doc["conflict_id"] = updated_doc.pop("_id")

        with patch("app.services.resolution.conflicts") as mock_col:
            # First find_one: fetch the unresolved conflict
            # Second find_one: return updated doc after $set
            mock_col.return_value.find_one = AsyncMock(
                side_effect=[dict(UNRESOLVED_CONFLICT), dict(RESOLVED_CONFLICT)]
            )
            mock_col.return_value.update_one = AsyncMock()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.patch("/conflicts/conflict_001/resolve", json=VALID_RESOLVE_BODY)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "resolved"
        assert data["resolution"]["chosen_source"] == "clinic_emr"
        assert data["resolution"]["reason"] == "Confirmed with prescribing physician."
        assert data["resolution"]["resolved_by"] == "dr_smith"

    @pytest.mark.asyncio
    async def test_successful_resolution_without_chosen_source(self, mock_db):
        """Clinician resolves without accepting either source — valid use case."""
        body = {
            "reason": "Discussed with patient. Neither source was accurate. Updated in EMR.",
            "resolved_by": "dr_jones",
        }
        resolved_no_source = {
            **UNRESOLVED_CONFLICT,
            "status": "resolved",
            "resolution": {
                "chosen_source": None,
                "reason": body["reason"],
                "resolved_by": "dr_jones",
                "resolved_at": "2026-03-10T12:00:00",
            },
        }

        with patch("app.services.resolution.conflicts") as mock_col:
            mock_col.return_value.find_one = AsyncMock(
                side_effect=[dict(UNRESOLVED_CONFLICT), dict(resolved_no_source)]
            )
            mock_col.return_value.update_one = AsyncMock()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.patch("/conflicts/conflict_001/resolve", json=body)

        assert response.status_code == 200
        data = response.json()
        assert data["resolution"]["chosen_source"] is None
        assert "Neither source" in data["resolution"]["reason"]

    @pytest.mark.asyncio
    async def test_resolving_already_resolved_returns_409(self, mock_db):
        """Re-resolving a clinical decision is not permitted."""
        with patch("app.services.resolution.conflicts") as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=dict(RESOLVED_CONFLICT))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.patch("/conflicts/conflict_001/resolve", json=VALID_RESOLVE_BODY)

        assert response.status_code == 409
        assert "already resolved" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_conflict_not_found_returns_404(self, mock_db):
        with patch("app.services.resolution.conflicts") as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=None)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.patch("/conflicts/ghost/resolve", json=VALID_RESOLVE_BODY)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_reason_returns_422(self, mock_db):
        body = {"resolved_by": "dr_smith"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.patch("/conflicts/conflict_001/resolve", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_resolved_by_returns_422(self, mock_db):
        body = {"reason": "confirmed"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.patch("/conflicts/conflict_001/resolve", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_chosen_source_returns_422(self, mock_db):
        body = {**VALID_RESOLVE_BODY, "chosen_source": "made_up_source"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.patch("/conflicts/conflict_001/resolve", json=body)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_body_returns_422(self, mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.patch("/conflicts/conflict_001/resolve", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_one_called_with_correct_fields(self, mock_db):
        """Verify the DB update sets both status and resolution atomically."""
        with patch("app.services.resolution.conflicts") as mock_col:
            update_mock = AsyncMock()
            mock_col.return_value.find_one = AsyncMock(
                side_effect=[dict(UNRESOLVED_CONFLICT), dict(RESOLVED_CONFLICT)]
            )
            mock_col.return_value.update_one = update_mock

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.patch("/conflicts/conflict_001/resolve", json=VALID_RESOLVE_BODY)

        # Verify the $set contains both status and resolution
        call_args = update_mock.call_args
        filter_doc = call_args[0][0]
        update_doc = call_args[0][1]
        assert filter_doc == {"_id": "conflict_001"}
        assert "status" in update_doc["$set"]
        assert "resolution" in update_doc["$set"]
        assert update_doc["$set"]["status"] == "resolved"