from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .vision import ChatMessage


ANCHOR_SIZE = 8
MAX_FORWARDS = 500


@dataclass
class RelayState:
    path: Path
    next_id: int = 1
    forwards: dict[str, dict] = field(default_factory=dict)
    anchors: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RelayState":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            next_id=int(data.get("next_id", 1)),
            forwards=dict(data.get("forwards", {})),
            anchors={k: list(v) for k, v in data.get("anchors", {}).items()},
        )

    def save(self) -> None:
        data = {"next_id": self.next_id, "forwards": self.forwards, "anchors": self.anchors}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def new_forward(self, chat: str, is_group: bool) -> int:
        fid = self.next_id
        self.next_id += 1
        self.forwards[str(fid)] = {"chat": chat, "group": is_group, "at": time.time()}
        if len(self.forwards) > MAX_FORWARDS:
            for key in sorted(self.forwards, key=int)[: len(self.forwards) - MAX_FORWARDS]:
                del self.forwards[key]
        return fid

    def forward_target(self, fid: int) -> str | None:
        item = self.forwards.get(str(fid))
        return item["chat"] if item else None

    def recent_chats(self, limit: int = 10) -> list[tuple[int, str]]:
        latest: dict[str, int] = {}
        for key in sorted(self.forwards, key=int):
            latest[self.forwards[key]["chat"]] = int(key)
        return sorted(((fid, chat) for chat, fid in latest.items()), reverse=True)[:limit]

    def take_new(self, chat: str, messages: list[ChatMessage]) -> tuple[list[ChatMessage], bool]:
        """Return messages after the stored anchor and update the anchor.

        The bool is False when the anchor was not found, i.e. the result is a best guess.
        """
        anchor = self.anchors.get(chat, [])
        sigs = [m.signature for m in messages]
        self.anchors[chat] = sigs[-ANCHOR_SIZE:]
        new, found = split_after_anchor(messages, sigs, anchor)
        return new, found


def split_after_anchor(
    messages: list[ChatMessage], sigs: list[str], anchor: list[str]
) -> tuple[list[ChatMessage], bool]:
    if anchor:
        # Longest suffix of the anchor that appears contiguously in the current view.
        min_len = 1 if len(anchor) == 1 else 2
        for size in range(len(anchor), min_len - 1, -1):
            tail = anchor[-size:]
            for end in range(len(sigs), size - 1, -1):
                if sigs[end - size : end] == tail:
                    return messages[end:], True
    # No usable anchor: treat everything after my own last message as unread.
    last_self = max((i for i, m in enumerate(messages) if m.side == "R"), default=-1)
    return messages[last_self + 1 :][-10:], False
