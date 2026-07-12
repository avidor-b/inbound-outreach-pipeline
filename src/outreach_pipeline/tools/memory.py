"""Long-term memory tools — cross-thread preferences and facts.

Uses LangGraph's Store API (PostgresStore) to persist data across
all threads for a user. Accessed via get_store() inside graph nodes.

Namespace convention: ("user", user_id, category)
  - ("user", "<user_id>", "preferences") — UI/behavior preferences
  - ("user", "<user_id>", "corrections") — user-corrected facts
  - ("user", "<user_id>", "contacts")   — contact-specific notes

user_id is extracted from the graph config (set by CopilotKit auth
headers). Falls back to "default" for unauthenticated sessions.
"""
from __future__ import annotations

from typing import Any


def _get_store():
    """Get the LangGraph store, returning None if unavailable (e.g. langgraph dev)."""
    try:
        from langgraph.config import get_store
        return get_store()
    except Exception:
        return None


def _get_user_id() -> str:
    """Extract user_id from the graph's RunnableConfig, or 'default'."""
    try:
        from langgraph.config import get_config
        config = get_config()
        return config.get("configurable", {}).get("user_id", "default")
    except Exception:
        return "default"


def _ns(category: str) -> tuple[str, str, str]:
    """Build a user-scoped namespace tuple."""
    return ("user", _get_user_id(), category)


_NO_STORE = {"success": False, "error": "Memory store not available (use production server)"}


async def save_preference(
    key: str,
    value: str,
) -> dict[str, Any]:
    """Save a user preference that persists across all threads.

    Examples: 'always show confidence scores', 'default date range is 7 days',
    'prefer concise responses'.

    key: short identifier (e.g. 'date_range', 'verbosity')
    value: the preference value (e.g. '7 days', 'concise')
    """
    store = _get_store()
    if not store:
        return _NO_STORE
    await store.aput(_ns("preferences"), key, {"value": value})
    return {"success": True, "key": key, "value": value}


async def get_preference(
    key: str,
) -> dict[str, Any]:
    """Get a saved user preference. Returns empty value if not set."""
    store = _get_store()
    if not store:
        return {"key": key, "value": "", "found": False}
    item = await store.aget(_ns("preferences"), key)
    if item:
        return {"key": key, "value": item.value.get("value", ""), "found": True}
    return {"key": key, "value": "", "found": False}


async def list_preferences() -> dict[str, Any]:
    """List all saved user preferences."""
    store = _get_store()
    if not store:
        return {"preferences": {}, "count": 0}
    items = await store.asearch(_ns("preferences"), limit=100)
    prefs = {item.key: item.value.get("value", "") for item in items}
    return {"preferences": prefs, "count": len(prefs)}


async def save_correction(
    key: str,
    value: str,
) -> dict[str, Any]:
    """Save a user-corrected fact that persists across threads.

    Examples: 'Example Networks operates in Region A, not Region B',
    'Example Telecom uses the outdoor radio family'.

    key: what's being corrected (e.g. 'example_networks_location')
    value: the correct information
    """
    store = _get_store()
    if not store:
        return _NO_STORE
    await store.aput(_ns("corrections"), key, {"value": value})
    return {"success": True, "key": key, "value": value}


async def get_corrections() -> dict[str, Any]:
    """Get all saved corrections."""
    store = _get_store()
    if not store:
        return {"corrections": {}, "count": 0}
    items = await store.asearch(_ns("corrections"), limit=100)
    corrections = {item.key: item.value.get("value", "") for item in items}
    return {"corrections": corrections, "count": len(corrections)}


async def save_contact_note(
    contact_id: str,
    note: str,
) -> dict[str, Any]:
    """Save a note about a specific contact that persists across threads.

    Examples: 'Key decision maker', 'Already has configured radio units',
    'Prefers email over calls'.
    """
    store = _get_store()
    if not store:
        return _NO_STORE
    await store.aput(_ns("contacts"), contact_id, {"value": note})
    return {"success": True, "contact_id": contact_id, "note": note}


async def get_contact_note(
    contact_id: str,
) -> dict[str, Any]:
    """Get saved notes about a contact."""
    store = _get_store()
    if not store:
        return {"contact_id": contact_id, "note": "", "found": False}
    item = await store.aget(_ns("contacts"), contact_id)
    if item:
        return {"contact_id": contact_id, "note": item.value.get("value", ""), "found": True}
    return {"contact_id": contact_id, "note": "", "found": False}
