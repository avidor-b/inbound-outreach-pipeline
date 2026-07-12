"""Async helpers — timeout-wrapped to_thread for sync operations."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def run_sync(fn: Callable, *args: Any, timeout: float = 15.0, **kwargs: Any) -> Any:
    """Run a sync function in a thread with a timeout.

    Returns None on timeout instead of crashing. The caller should
    handle None gracefully (most tool functions already do).

    Usage:
        result = await run_sync(web_search, "query", max_results=5)
        result = await run_sync(adapter.query, "5G backhaul", timeout=30)
    """
    if kwargs:
        coro = asyncio.to_thread(lambda: fn(*args, **kwargs))
    else:
        coro = asyncio.to_thread(fn, *args)
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        fn_name = getattr(fn, '__name__', str(fn))
        logger.warning("run_sync timeout after %ds: %s", timeout, fn_name)
        return None
