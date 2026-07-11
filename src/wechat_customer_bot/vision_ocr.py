from __future__ import annotations

import base64
import io
import json
from typing import Any

import requests
from PIL import Image

from .config import AppConfig
from .events import ChatEvent, event_from_dict
from .http_client import post_json_with_retry


class VisionOcr:
    def __init__(self, config: AppConfig):
        self.config = config

    def extract_events(self, image: Image.Image, window_title: str) -> list[ChatEvent]:
        if not self.config.llm_api_key:
            raise RuntimeError("BOT_LLM_API_KEY is not configured")
        payload = self._payload(image, window_title)
        res = post_json_with_retry(
            f"{self.config.llm_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.llm_api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
            payload=payload,
        )
        if res.status_code >= 400:
            raise RuntimeError(f"vision OCR failed: {res.status_code} {res.text[:300]}")
        data = res.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = parse_json_object(content)
        rows = parsed.get("messages", []) if isinstance(parsed, dict) else []
        if not isinstance(rows, list):
            return []
        return [event_from_dict(row) for row in rows if isinstance(row, dict)]

    def _payload(self, image: Image.Image, window_title: str) -> dict[str, Any]:
        data_url = image_to_data_url(image)
        prompt = "\n".join(
            [
                "You are reading a cropped Windows WeChat chat area.",
                "Return only JSON. Do not add markdown.",
                "Identify only the latest visible message blocks from bottom to top, then output top-to-bottom order.",
                "Classify role by bubble side: left is customer, right is self, center gray is system.",
                "Do not invent text. If uncertain, use role=unknown or confidence below 0.6.",
                "For stickers/images, set type=emoji or image and summarize briefly.",
                "For voice-message bubbles, set type=voice and text to a brief marker such as [voice], unless a visible transcript is shown.",
                "Ignore timestamps and system separators unless they are important.",
                "JSON schema:",
                '{"messages":[{"role":"customer|self|system|unknown","type":"text|image|emoji|voice|mixed|system|unknown","text":"string","confidence":0.0,"bbox":[x,y,w,h]}]}',
                f"WeChat window title: {window_title}",
            ]
        )
        return {
            "model": self.config.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 800,
            "stream": False,
        }


def image_to_data_url(image: Image.Image) -> str:
    max_side = 1400
    if max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    return {}
