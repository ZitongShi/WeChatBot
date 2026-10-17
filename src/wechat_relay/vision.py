from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image


MESSAGE_TYPES = {"text", "image", "voice", "video", "file", "link", "sticker", "system", "other"}

PROMPT = """You are reading a screenshot of one open WeChat desktop chat: the title bar on top, the messages below.
Return JSON only, no markdown:
{"title": str, "member_count": int|null, "group": bool, "messages": [{"side": "L|R|C", "sender": str, "type": str, "text": str, "quote": str, "box": [x1,y1,x2,y2]}]}

Rules:
- title: the chat name in the title bar exactly as shown, without a trailing "(N)" member count.
- member_count: N when the title bar shows "(N)" after the name (group chats), else null.
- List every visible message bubble from top to bottom. Skip timestamps.
- side: L = bubble on the left (sent by someone else), R = bubble on the right (sent by me), C = centered gray system notice.
- group: true if small gray nickname labels appear above the left bubbles.
- sender: for L bubbles in a group, the nickname label shown above that bubble; otherwise "".
- type: one of text, image, voice, video, file, link, sticker, system, other.
- text: exact visible text for text bubbles; file name for files; title for links; for voice, the transcript if one is shown under the bubble, else ""; a 2-6 word description for images, videos and stickers.
- quote: if the bubble has a quoted/replied message in a gray box under it, the quoted text exactly; else "".
  The gray quoted box belongs to the bubble above it: never output it as a message of its own.
- box: bubble bounding box in 0-1000 coordinates relative to this screenshot, for every message.
- Never invent text you cannot read."""


@dataclass
class ChatMessage:
    side: str
    sender: str
    type: str
    text: str
    quote: str = ""
    box: tuple[int, int, int, int] | None = None

    @property
    def signature(self) -> str:
        return "|".join([self.side, self.type, normalize_for_match(self.text)[:40]])


@dataclass
class ChatRead:
    is_group: bool
    messages: list[ChatMessage]
    title: str = ""
    member_count: int | None = None


@dataclass
class Row:
    """A clickable row the vision model found: session-list entry or search result."""

    name: str
    center: tuple[int, int]
    section: str = ""


def normalize_for_match(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


def image_to_data_url(image: Image.Image, max_side: int = 1280) -> str:
    image = image.convert("RGB")
    if max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def parse_chat_read(data: dict[str, Any], image_size: tuple[int, int]) -> ChatRead:
    width, height = image_size
    messages: list[ChatMessage] = []
    for row in data.get("messages") or []:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or "L").upper()[:1]
        if side not in {"L", "R", "C"}:
            side = "L"
        kind = str(row.get("type") or "other").lower()
        if kind not in MESSAGE_TYPES:
            kind = "other"
        box = None
        raw_box = row.get("box")
        if isinstance(raw_box, list) and len(raw_box) == 4:
            try:
                x1, y1, x2, y2 = (float(v) for v in raw_box)
                box = (
                    int(x1 / 1000 * width),
                    int(y1 / 1000 * height),
                    int(x2 / 1000 * width),
                    int(y2 / 1000 * height),
                )
            except (TypeError, ValueError):
                box = None
        if box and side != "C":
            # The model sometimes mislabels sides; bubble geometry is more reliable.
            left_gap, right_gap = box[0], width - box[2]
            if abs(left_gap - right_gap) > width * 0.03:
                side = "L" if left_gap < right_gap else "R"
        messages.append(
            ChatMessage(
                side=side,
                sender=str(row.get("sender") or "").strip(),
                type=kind,
                text=str(row.get("text") or "").strip(),
                quote=str(row.get("quote") or "").strip(),
                box=box,
            )
        )
    title, member_count = _split_title(data.get("title"), data.get("member_count"))
    return ChatRead(
        # The "(N)" after a group's title is far more reliable than nickname labels.
        is_group=member_count is not None,
        messages=messages,
        title=title,
        member_count=member_count,
    )


MY_BUBBLE = (157, 242, 159)  # WeChat 4 green for my own text bubbles
PANE_BG = (250, 250, 250)


def _close(color: tuple[int, ...], target: tuple[int, int, int], tol: int) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(color, target))


def fix_sides(read: ChatRead, image: Image.Image) -> None:
    """Decide left/right from pixels, not from the model.

    A message of mine misread as incoming would be relayed (or, in the main chat, run as a
    command and answered by the bot itself), so the model's side is only the last resort:
    green fill means mine, else the avatar beside the bubble's top says which side.
    """
    img = image.convert("RGB")
    width, height = img.size
    px = img.load()
    left_x, right_x = 47, width - 49  # avatar columns
    for message in read.messages:
        if message.side == "C" or not message.box:
            continue
        x1, y1, x2, y2 = (max(0, v) for v in message.box)
        x2, y2 = min(x2, width - 1), min(y2, height - 1)
        if x2 <= x1 or y2 <= y1:
            continue
        samples = [px[x, y] for x in range(x1, x2, 4) for y in range(y1, y2, 4)]
        if samples and sum(_close(c, MY_BUBBLE, 12) for c in samples) >= 0.2 * len(samples):
            message.side = "R"
            continue
        ys = range(max(0, y1 - 8), min(height, y1 + 40))
        left = sum(not _close(px[left_x, y], PANE_BG, 6) for y in ys)
        right = sum(not _close(px[right_x, y], PANE_BG, 6) for y in ys)
        if left >= 5 and right == 0:
            message.side = "L"
        elif right >= 5 and left == 0:
            message.side = "R"


def _split_title(raw_title: Any, raw_count: Any) -> tuple[str, int | None]:
    title = str(raw_title or "").strip()
    count: int | None = None
    # Models sometimes keep the count in the title despite the instructions.
    match = re.search(r"[(（]\s*(\d+)\s*[)）]\s*$", title)
    if match:
        title = title[: match.start()].strip()
        count = int(match.group(1))
    try:
        if raw_count is not None and int(raw_count) > 0:
            count = int(raw_count)
    except (TypeError, ValueError):
        pass
    return title, count


def _box_center(raw_box: Any, size: tuple[int, int]) -> tuple[int, int] | None:
    if not isinstance(raw_box, list) or len(raw_box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in raw_box)
    except (TypeError, ValueError):
        return None
    cx, cy = (x1 + x2) / 2000, (y1 + y2) / 2000
    if not (0 <= cx <= 1 and 0 <= cy <= 1):
        return None
    return int(cx * size[0]), int(cy * size[1])


def parse_rows(data: dict[str, Any], size: tuple[int, int]) -> list[Row]:
    rows: list[Row] = []
    for item in data.get("rows") or []:
        if not isinstance(item, dict):
            continue
        center = _box_center(item.get("box"), size)
        name = str(item.get("name") or "").strip()
        if center and name:
            rows.append(Row(name=name, center=center, section=str(item.get("section") or "").strip()))
    return rows


SESSIONS_PROMPT = """This is the session list of the WeChat desktop app: one row per chat, each with an
avatar, a bold name, a timestamp and a gray preview line.
Return JSON only: {"rows": [{"name": str, "box": [x1,y1,x2,y2]}]}
- One entry per row, top to bottom, including partly visible rows at the edges.
- name: the bold chat name exactly as shown (keep a trailing "..." if it is cut off). Never the preview text.
- box: the whole row, in 0-1000 coordinates relative to this image."""

SEARCH_PROMPT = """This is the WeChat desktop search-result popup. Results are grouped under gray section
headers such as 联系人, 群聊, 功能, 最常使用, 搜索网络结果, 聊天记录.
Return JSON only: {"rows": [{"section": str, "name": str, "box": [x1,y1,x2,y2]}]}
- One entry per result row, top to bottom. section: the header the row sits under, as shown.
- name: the row's main title exactly as shown (part of it may be green). Never the gray second line.
- box: the whole row, in 0-1000 coordinates relative to this image."""

TITLE_PROMPT = """This is the title bar of an open WeChat desktop chat.
Return JSON only: {"title": str, "member_count": int|null}
- title: the chat name exactly as shown, without a trailing "(N)"; "" if no chat is open.
- member_count: N when "(N)" follows the name, else null."""


class VisionReader:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 120):
        if not base_url or not api_key:
            raise RuntimeError("未配置 SOCLAAS_BASE_URL / SOCLAAS_API_KEY")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _ask(self, prompt: str, image: Image.Image, max_tokens: int) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
                    ],
                }
            ],
        }
        last_error: Exception | None = None
        for _ in range(2):
            try:
                res = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
                if res.status_code >= 400:
                    raise RuntimeError(f"vision {res.status_code}: {res.text[:200]}")
                return parse_json_object(res.json()["choices"][0]["message"]["content"])
            except (requests.RequestException, RuntimeError, KeyError, IndexError) as exc:
                last_error = exc
        raise RuntimeError(f"视觉模型调用失败: {last_error}")

    def read_chat(self, image: Image.Image) -> ChatRead:
        """Read title and messages from the chat pane (title bar down to the input box)."""
        read = parse_chat_read(self._ask(PROMPT, image, 2000), image.size)
        fix_sides(read, image)
        return read

    def read_title(self, image: Image.Image) -> tuple[str, int | None]:
        data = self._ask(TITLE_PROMPT, image, 100)
        return _split_title(data.get("title"), data.get("member_count"))

    def session_rows(self, image: Image.Image) -> list[Row]:
        return parse_rows(self._ask(SESSIONS_PROMPT, image, 800), image.size)

    def search_rows(self, image: Image.Image) -> list[Row]:
        return parse_rows(self._ask(SEARCH_PROMPT, image, 800), image.size)
