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

PROMPT = """You are reading a screenshot of the message area of one WeChat desktop chat.
Return JSON only, no markdown:
{"group": bool, "messages": [{"side": "L|R|C", "sender": str, "type": str, "text": str, "quote": str, "box": [x1,y1,x2,y2]}]}

Rules:
- List every visible message bubble from top to bottom. Skip timestamps.
- side: L = bubble on the left (sent by someone else), R = bubble on the right (sent by me), C = centered gray system notice.
- group: true if small gray nickname labels appear above the left bubbles.
- sender: for L bubbles in a group, the nickname label shown above that bubble; otherwise "".
- type: one of text, image, voice, video, file, link, sticker, system, other.
- text: exact visible text for text bubbles; file name for files; title for links; for voice, the transcript if one is shown under the bubble, else ""; a 2-6 word description for images, videos and stickers.
- quote: if the bubble has a quoted/replied message in a gray box under it, the quoted text exactly; else "".
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
    return ChatRead(is_group=bool(data.get("group")), messages=messages)


class VisionReader:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 120):
        if not base_url or not api_key:
            raise RuntimeError("未配置 SOCLAAS_BASE_URL / SOCLAAS_API_KEY")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def read_chat(self, image: Image.Image) -> ChatRead:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
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
                content = res.json()["choices"][0]["message"]["content"]
                return parse_chat_read(parse_json_object(content), image.size)
            except (requests.RequestException, RuntimeError, KeyError) as exc:
                last_error = exc
        raise RuntimeError(f"视觉模型调用失败: {last_error}")
