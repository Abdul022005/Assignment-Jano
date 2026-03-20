"""
Conflicts router.

Endpoints:
  GET   /conflicts/{conflict_id}          Get a single conflict by ID
  PATCH /conflicts/{conflict_id}/resolve  Resolve a conflict
"""

from fastapi import APIRouter, HTTPException, status

from app.db.collections import conflicts
from app.models.conflict import ConflictResolveRequest
from app.services.resolution import (
    ConflictAlreadyResolvedError,
    ConflictNotFoundError,
    resolve_conflict,
)

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


@router.get("/{conflict_id}")
async def get_conflict(conflict_id: str) -> dict:
    """Retrieve a single conflict by ID."""
    doc = await conflicts().find_one({"_id": conflict_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflict not found.")
    doc["conflict_id"] = doc.pop("_id")
    return doc


@router.patch("/{conflict_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_conflict_endpoint(
    conflict_id: str,
    body: ConflictResolveRequest,
) -> dict:
    """
    Mark a conflict as resolved.

    The clinician must supply:
      - `reason`        — free-text clinical rationale (required)
      - `resolved_by`   — clinician identifier (required)
      - `chosen_source` — which source's version was accepted (optional;
                          may be null if neither source was correct)

    Returns the updated conflict document with the embedded resolution.
    """
    try:
        updated = await resolve_conflict(conflict_id, body)
    except ConflictNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ConflictAlreadyResolvedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return updated