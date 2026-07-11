from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


VALID_ROLES = {"customer", "self", "system", "unknown"}
VALID_TYPES = {"text", "image", "emoji", "voice", "mixed", "system", "unknown"}


@dataclass
class ChatEvent:
    role: str
    type: str
    text: str
    confidence: float = 0.0
    bbox: list[int] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    seen_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def normalized_text(self) -> str:
        return normalize_text(self.text)

    @property
    def is_customer_text(self) -> bool:
        return self.role == "customer" and self.type in {"text", "mixed"} and bool(self.normalized_text)

    @property
    def is_customer_observable(self) -> bool:
        return self.role == "customer" and self.type in {"text", "mixed", "image", "emoji", "voice"} and (
            bool(self.normalized_text) or self.type in {"image", "emoji", "voice"}
        )

    def fingerprint(self) -> str:
        bbox_bucket = ""
        if self.bbox and len(self.bbox) >= 4:
            bbox_bucket = ":".join(str(round(int(v) / 20) * 20) for v in self.bbox[:4])
        raw = "|".join([self.role, self.type, self.normalized_text, bbox_bucket])
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def normalize_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = value.replace("，", ",").replace("。", ".")
    return value


def event_from_dict(data: dict[str, Any]) -> ChatEvent:
    role = str(data.get("role") or "unknown").lower()
    kind = str(data.get("type") or data.get("kind") or "unknown").lower()
    if role not in VALID_ROLES:
        role = "unknown"
    if kind not in VALID_TYPES:
        kind = "unknown"
    text = str(data.get("text") or data.get("ocrText") or data.get("summary") or "").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except Exception:
        confidence = 0.0
    bbox = data.get("bbox")
    if not isinstance(bbox, list):
        bbox = None
    return ChatEvent(role=role, type=kind, text=text, confidence=confidence, bbox=bbox, raw=data)


def compact_events(events: list[ChatEvent]) -> str:
    parts = []
    for event in events:
        text = event.normalized_text or f"[{event.type}]"
        parts.append(f"{event.role}:{text}")
    return "\n".join(parts)
