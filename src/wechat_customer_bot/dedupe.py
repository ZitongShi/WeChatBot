from __future__ import annotations

from dataclasses import dataclass

from .events import ChatEvent


@dataclass
class SeenState:
    count: int = 0
    processed: bool = False


class MessageDeduper:
    def __init__(self, stable_seen_required: int = 2, min_confidence: float = 0.65):
        self.stable_seen_required = max(1, stable_seen_required)
        self.min_confidence = min_confidence
        self._seen: dict[str, SeenState] = {}

    def accept(self, events: list[ChatEvent]) -> list[ChatEvent]:
        accepted: list[ChatEvent] = []
        for event in events:
            if not event.is_customer_observable:
                continue
            if event.confidence < self.min_confidence:
                continue
            fp = event.fingerprint()
            state = self._seen.setdefault(fp, SeenState())
            state.count += 1
            if state.processed:
                continue
            if state.count >= self.stable_seen_required:
                state.processed = True
                accepted.append(event)
        return accepted
