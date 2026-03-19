"""
Resolution service.

Responsible for:
  1. Validating a conflict exists and is currently unresolved
  2. Applying the clinician's resolution (chosen_source, reason, resolved_by)
  3. Persisting the update atomically
  4. Returning the updated Conflict document

Design decisions documented here:
  - Resolution is a single atomic $set — the conflict document is updated
    in place rather than creating a new document. This is safe because
    the resolution sub-document is append-only (once set it is never changed).
    If re-opening a resolved conflict is needed in the future, a resolution
    history array would be the right extension.
  - Re-resolving an already-resolved conflict is explicitly rejected (409).
    Silently overwriting a clinical decision without a trace would be unsafe.
"""

from datetime import datetime, timezone

from app.db.collections import conflicts
from app.models.common import ConflictStatus
from app.models.conflict import ConflictResolveRequest


class ConflictNotFoundError(Exception):
    pass


class ConflictAlreadyResolvedError(Exception):
    pass


async def resolve_conflict(
    conflict_id: str,
    request: ConflictResolveRequest,
) -> dict:
    """
    Mark a conflict as resolved with full audit metadata.

    Returns the updated conflict document.
    Raises ConflictNotFoundError if the conflict_id does not exist.
    Raises ConflictAlreadyResolvedError if already resolved.
    """
    doc = await conflicts().find_one({"_id": conflict_id})
    if doc is None:
        raise ConflictNotFoundError(f"Conflict '{conflict_id}' not found.")

    if doc.get("status") == ConflictStatus.RESOLVED:
        raise ConflictAlreadyResolvedError(
            f"Conflict '{conflict_id}' is already resolved. "
            "Re-resolving a clinical decision is not permitted."
        )

    resolution_doc = {
        "chosen_source": request.chosen_source.value if request.chosen_source else None,
        "reason": request.reason,
        "resolved_by": request.resolved_by,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }

    await conflicts().update_one(
        {"_id": conflict_id},
        {
            "$set": {
                "status": ConflictStatus.RESOLVED.value,
                "resolution": resolution_doc,
            }
        },
    )

    # Return the updated document
    updated = await conflicts().find_one({"_id": conflict_id})
    updated["conflict_id"] = updated.pop("_id")
    return updated