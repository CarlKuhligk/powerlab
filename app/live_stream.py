"""A bounded, thread-to-async notification for an individual live viewer."""
from __future__ import annotations

import asyncio
import threading


class LiveSubscription:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()
        self.lock = threading.Lock()
        self.pending = False
        self.closed = False
        self.cursor = -1
        self.spectral_cursor = -1
        self.epoch = None

    def notify(self) -> None:
        with self.lock:
            if self.closed or self.pending:
                return
            self.pending = True
            self.loop.call_soon_threadsafe(self.event.set)

    async def wait(self) -> None:
        await self.event.wait()
        with self.lock:
            self.event.clear()
            self.pending = False

    def close(self) -> None:
        with self.lock:
            self.closed = True
