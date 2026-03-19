from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.client import connect, disconnect, get_db
from app.db.indexes import ensure_indexes


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    await ensure_indexes(get_db())
    yield
    await disconnect()


app = FastAPI(
    title="Medication Reconciliation & Conflict Reporting Service",
    description="Ingests medication lists from multiple sources, detects conflicts, and surfaces them for clinicians.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
async def health_check():
    return {"status": "ok"}