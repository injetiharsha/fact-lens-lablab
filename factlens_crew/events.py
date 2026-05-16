"""In-memory run event store for the War Room UI."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, List

from .schemas import WarRoomEvent


class EventStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Dict[str, List[WarRoomEvent]] = defaultdict(list)

    def add(self, event: WarRoomEvent) -> None:
        with self._lock:
            self._events[event.run_id].append(event)

    def list(self, run_id: str) -> List[dict]:
        with self._lock:
            return [event.to_dict() for event in self._events.get(run_id, [])]


event_store = EventStore()

