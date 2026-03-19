from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError("MongoDB client not initialised. Call connect() first.")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_db_name]


async def connect() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.mongodb_uri)
    # Verify connection is reachable
    await _client.admin.command("ping")


async def disconnect() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None