"""
Typed accessors for every MongoDB collection.

Import these functions instead of calling db["collection_name"] directly.
Benefits:
  - Collection names are defined in exactly one place — rename here, fixed everywhere.
  - Motor's AsyncIOMotorCollection type flows through to callers for IDE support.
  - Easy to swap in a test database by patching get_db() in tests.
"""

from motor.motor_asyncio import AsyncIOMotorCollection

from app.db.client import get_db


def patients() -> AsyncIOMotorCollection:
    return get_db()["patients"]


def medication_snapshots() -> AsyncIOMotorCollection:
    return get_db()["medication_snapshots"]


def conflicts() -> AsyncIOMotorCollection:
    return get_db()["conflicts"]


def clinics() -> AsyncIOMotorCollection:
    return get_db()["clinics"]