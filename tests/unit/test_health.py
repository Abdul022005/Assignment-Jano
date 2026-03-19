import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    # Patch DB connect/disconnect so the test never needs a real MongoDB
    with patch("app.main.connect", new_callable=AsyncMock), \
         patch("app.main.disconnect", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}