"""
Integration tests for reporting / aggregation endpoints.

Covers:
  - GET /clinics/{id}/patients/unresolved-conflicts
      - returns correct patients and counts
      - min_conflicts query param filters correctly
      - clinic not found → 404
      - no matching patients → empty list
      - min_conflicts=1 vs min_conflicts=2 returns different result sets

  - GET /clinics/{id}/reports/conflicts-last-30-days
      - returns correct summary shape
      - days and min_conflicts params are respected
      - clinic not found → 404
      - invalid params (days=0, days=400) → 422

Also includes a unit test for the aggregation pipeline logic itself,
verifying the pipeline stages are structured correctly.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.reporting import (
    patients_with_unresolved_conflicts,
    conflict_counts_last_n_days,
)

# ------------------------------------------------------------------ #
# Shared fixtures
# ------------------------------------------------------------------ #

CLINIC_DOC = {"_id": "clinic_a", "name": "Kochi Dialysis Center", "location": "Kochi, Kerala"}

# Aggregation results as MongoDB would return them (after $project)
UNRESOLVED_RESULTS = [
    {
        "patient_id": "p001",
        "patient_name": "Jane Doe",
        "clinic_id": "clinic_a",
        "unresolved_conflict_count": 3,
        "conflict_types": ["dose_mismatch", "stopped_vs_active"],
        "oldest_unresolved_conflict": "2026-02-01T10:00:00",
    },
    {
        "patient_id": "p002",
        "patient_name": "John Smith",
        "clinic_id": "clinic_a",
        "unresolved_conflict_count": 1,
        "conflict_types": ["class_conflict"],
        "oldest_unresolved_conflict": "2026-03-01T10:00:00",
    },
]

REPORT_RESULTS = [
    {
        "patient_id": "p001",
        "patient_name": "Jane Doe",
        "total_conflicts": 4,
        "unresolved_count": 3,
        "resolved_count": 1,
        "conflict_types": ["dose_mismatch"],
    },
    {
        "patient_id": "p003",
        "patient_name": "Alice Kumar",
        "total_conflicts": 2,
        "unresolved_count": 2,
        "resolved_count": 0,
        "conflict_types": ["stopped_vs_active"],
    },
]


@pytest.fixture
def mock_db():
    with patch("app.main.connect", new_callable=AsyncMock), \
         patch("app.main.disconnect", new_callable=AsyncMock), \
         patch("app.main.get_db", return_value=MagicMock()), \
         patch("app.db.indexes.ensure_indexes", new_callable=AsyncMock):
        yield


def make_async_agg(docs: list[dict]):
    """Return a mock object that supports async iteration."""
    async def _aiter(self):
        for doc in docs:
            yield doc
    mock = MagicMock()
    mock.__aiter__ = _aiter
    return mock


# ------------------------------------------------------------------ #
# GET /clinics/{clinic_id}/patients/unresolved-conflicts
# ------------------------------------------------------------------ #

class TestUnresolvedConflictsEndpoint:

    @pytest.mark.asyncio
    async def test_returns_correct_patients(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(UNRESOLVED_RESULTS)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/patients/unresolved-conflicts")

        assert response.status_code == 200
        data = response.json()
        assert data["clinic_id"] == "clinic_a"
        assert data["clinic_name"] == "Kochi Dialysis Center"
        assert data["matching_patient_count"] == 2
        assert data["patients"][0]["patient_id"] == "p001"
        assert data["patients"][0]["unresolved_conflict_count"] == 3

    @pytest.mark.asyncio
    async def test_min_conflicts_filters_results(self, mock_db):
        """min_conflicts=2 should exclude the patient with only 1 conflict."""
        # Simulate the aggregation pipeline already filtering — return only p001
        filtered = [UNRESOLVED_RESULTS[0]]  # only Jane with 3 conflicts

        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(filtered)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/patients/unresolved-conflicts?min_conflicts=2")

        assert response.status_code == 200
        data = response.json()
        assert data["min_conflicts"] == 2
        assert data["matching_patient_count"] == 1
        assert data["patients"][0]["patient_id"] == "p001"

    @pytest.mark.asyncio
    async def test_no_matching_patients_returns_empty_list(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg([])

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/patients/unresolved-conflicts")

        assert response.status_code == 200
        data = response.json()
        assert data["matching_patient_count"] == 0
        assert data["patients"] == []

    @pytest.mark.asyncio
    async def test_clinic_not_found_returns_404(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics:
            mock_clinics.return_value.find_one = AsyncMock(return_value=None)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/ghost/patients/unresolved-conflicts")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_min_conflicts_zero_returns_422(self, mock_db):
        """min_conflicts must be >= 1."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/clinics/clinic_a/patients/unresolved-conflicts?min_conflicts=0")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_response_includes_conflict_types(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(UNRESOLVED_RESULTS)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/patients/unresolved-conflicts")

        data = response.json()
        conflict_types = data["patients"][0]["conflict_types"]
        assert isinstance(conflict_types, list)
        assert len(conflict_types) > 0


# ------------------------------------------------------------------ #
# GET /clinics/{clinic_id}/reports/conflicts-last-30-days
# ------------------------------------------------------------------ #

class TestConflictReportEndpoint:

    @pytest.mark.asyncio
    async def test_returns_correct_summary(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(REPORT_RESULTS)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days")

        assert response.status_code == 200
        data = response.json()
        assert data["clinic_id"] == "clinic_a"
        assert data["clinic_name"] == "Kochi Dialysis Center"
        assert data["window_days"] == 30
        assert data["min_conflicts"] == 2
        assert data["matching_patient_count"] == 2
        assert "since" in data
        assert len(data["patients"]) == 2

    @pytest.mark.asyncio
    async def test_custom_days_param(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(REPORT_RESULTS)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days?days=7")

        assert response.status_code == 200
        assert response.json()["window_days"] == 7

    @pytest.mark.asyncio
    async def test_custom_min_conflicts_param(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg([REPORT_RESULTS[0]])

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get(
                    "/clinics/clinic_a/reports/conflicts-last-30-days?min_conflicts=3"
                )

        assert response.status_code == 200
        assert response.json()["min_conflicts"] == 3
        assert response.json()["matching_patient_count"] == 1

    @pytest.mark.asyncio
    async def test_clinic_not_found_returns_404(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics:
            mock_clinics.return_value.find_one = AsyncMock(return_value=None)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/ghost/reports/conflicts-last-30-days")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_days_zero_returns_422(self, mock_db):
        """days must be >= 1."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days?days=0")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_days_over_365_returns_422(self, mock_db):
        """days must be <= 365."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days?days=400")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_clinic_returns_zero_patients(self, mock_db):
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg([])

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days")

        data = response.json()
        assert data["matching_patient_count"] == 0
        assert data["patients"] == []

    @pytest.mark.asyncio
    async def test_per_patient_breakdown_shape(self, mock_db):
        """Each patient entry must have total, unresolved, and resolved counts."""
        with patch("app.api.routes.clinics.clinics") as mock_clinics, \
             patch("app.services.reporting.conflicts") as mock_conflicts:

            mock_clinics.return_value.find_one = AsyncMock(return_value=CLINIC_DOC)
            mock_conflicts.return_value.aggregate.return_value = make_async_agg(REPORT_RESULTS)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.get("/clinics/clinic_a/reports/conflicts-last-30-days")

        patient = response.json()["patients"][0]
        assert "patient_id" in patient
        assert "patient_name" in patient
        assert "total_conflicts" in patient
        assert "unresolved_count" in patient
        assert "resolved_count" in patient
        assert "conflict_types" in patient


# ------------------------------------------------------------------ #
# Unit test — aggregation pipeline structure
# ------------------------------------------------------------------ #

class TestAggregationPipelineStructure:
    """
    Verify that the aggregation service calls .aggregate() with a pipeline
    containing the correct stages. This tests the pipeline construction
    without needing a real MongoDB.
    """

    @pytest.mark.asyncio
    async def test_unresolved_pipeline_passes_clinic_id_to_match(self):
        with patch("app.services.reporting.conflicts") as mock_col:
            mock_col.return_value.aggregate.return_value = make_async_agg([])
            await patients_with_unresolved_conflicts("clinic_x", min_conflicts=1)

        pipeline = mock_col.return_value.aggregate.call_args[0][0]
        first_match = pipeline[0]["$match"]
        assert first_match["clinic_id"] == "clinic_x"
        assert first_match["status"] == "unresolved"

    @pytest.mark.asyncio
    async def test_unresolved_pipeline_threshold_in_second_match(self):
        with patch("app.services.reporting.conflicts") as mock_col:
            mock_col.return_value.aggregate.return_value = make_async_agg([])
            await patients_with_unresolved_conflicts("clinic_x", min_conflicts=3)

        pipeline = mock_col.return_value.aggregate.call_args[0][0]
        # Find the $match stage that filters by count
        count_match = next(
            s["$match"] for s in pipeline
            if "$match" in s and "unresolved_count" in s["$match"]
        )
        assert count_match["unresolved_count"] == {"$gte": 3}

    @pytest.mark.asyncio
    async def test_report_pipeline_uses_date_window(self):
        with patch("app.services.reporting.conflicts") as mock_col:
            mock_col.return_value.aggregate.return_value = make_async_agg([])
            await conflict_counts_last_n_days("clinic_x", days=7, min_conflicts=2)

        pipeline = mock_col.return_value.aggregate.call_args[0][0]
        first_match = pipeline[0]["$match"]
        assert first_match["clinic_id"] == "clinic_x"
        assert "$gte" in first_match["detected_at"]

        # The date should be approximately 7 days ago
        since = first_match["detected_at"]["$gte"]
        expected = datetime.now(timezone.utc) - timedelta(days=7)
        diff = abs((since - expected).total_seconds())
        assert diff < 5  # within 5 seconds

    @pytest.mark.asyncio
    async def test_report_pipeline_contains_lookup_stage(self):
        """Pipeline must join patient names — not fetch them separately."""
        with patch("app.services.reporting.conflicts") as mock_col:
            mock_col.return_value.aggregate.return_value = make_async_agg([])
            await conflict_counts_last_n_days("clinic_x", days=30, min_conflicts=2)

        pipeline = mock_col.return_value.aggregate.call_args[0][0]
        lookup_stages = [s for s in pipeline if "$lookup" in s]
        assert len(lookup_stages) == 1
        assert lookup_stages[0]["$lookup"]["from"] == "patients"