"""Space request starts evenly so a per-minute quota is not exceeded."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class Pacer:
    def __init__(
        self,
        per_minute: int | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = 60.0 / per_minute if per_minute else 0.0
        self._next_slot = 0.0
        self._sleep = sleep

    async def wait(self) -> None:
        """Return when the next request may start. No-op when pacing is off."""
        if not self._interval:
            return
        now = asyncio.get_running_loop().time()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)
