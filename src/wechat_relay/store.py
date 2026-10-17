from __future__ import annotations

import difflib
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


def _same_sig(a: str, b: str) -> bool:
    """Signatures match; the text part may differ by a misread glyph between two vision reads."""
    if a == b:
        return True
    a_head, _, a_text = a.rpartition("|")
    b_head, _, b_text = b.rpartition("|")
    if a_head != b_head or min(len(a_text), len(b_text)) < 4:
        return False
    return difflib.SequenceMatcher(None, a_text, b_text).ratio() >= 0.8


def _distinctive(sig: str) -> bool:
    """Long enough to anchor on by itself ("ok" or "好" could be any message)."""
    return len(sig.rpartition("|")[2]) >= 4


def split_after_anchor(
    messages: list[ChatMessage], sigs: list[str], anchor: list[str]
) -> tuple[list[ChatMessage], bool]:
    if anchor:
        # Longest suffix of the anchor that appears contiguously in the current view.
        # A single message is enough when its text is distinctive and it appears only once,
        # e.g. after the rest of the anchor scrolled out of view.
        min_len = 1 if len(anchor) == 1 or _distinctive(anchor[-1]) else 2
        for size in range(len(anchor), min_len - 1, -1):
            tail = anchor[-size:]
            ends = [
                end
                for end in range(len(sigs), size - 1, -1)
                if all(_same_sig(x, y) for x, y in zip(sigs[end - size : end], tail))
            ]
            if ends and (size > 1 or len(anchor) == 1 or len(ends) == 1):
                return messages[ends[0] :], True
    # No usable anchor: treat everything after my own last message as unread.
    last_self = max((i for i, m in enumerate(messages) if m.side == "R"), default=-1)
    return messages[last_self + 1 :][-10:], False
