from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CropConfig:
    left_ratio: float = 0.0
    top_ratio: float = 0.10
    right_ratio: float = 1.0
    bottom_ratio: float = 0.83


@dataclass(frozen=True)
class AppConfig:
    llm_base_url: str
    llm_model: str
    vision_model: str
    reply_model: str
    llm_api_key: str
    poll_interval_sec: float
    stable_seen_required: int
    min_event_confidence: float
    message_debounce_sec: float
    chat_crop: CropConfig
    bottom_scan_ratio: float
    save_screenshots: bool
    activate_before_capture: bool
    suggest_replies: bool
    admin_confirm_required: bool
    max_recent_messages: int
    skill_path: Path
    data_dir: Path
    log_dir: Path


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_config(path: str | None = None) -> AppConfig:
    cfg_path = Path(path) if path else ROOT / "config.json"
    example = _read_json(ROOT / "config.example.json")
    user = _read_json(cfg_path)
    raw = {**example, **user}
    crop = {**example.get("chat_crop", {}), **user.get("chat_crop", {})}

    data_dir = Path(os.getenv("WECHAT_BOT_DATA_DIR", str(ROOT / "data")))
    log_dir = Path(os.getenv("WECHAT_BOT_LOG_DIR", str(ROOT / "logs")))
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "screens").mkdir(parents=True, exist_ok=True)
    default_skill_path = ROOT / "skills" / "wechat-tt-customer-service" / "SKILL.md"
    skill_path = Path(raw.get("skill_path") or default_skill_path)
    if not skill_path.is_absolute():
        skill_path = ROOT / skill_path

    return AppConfig(
        llm_base_url=(os.getenv("BOT_LLM_BASE_URL") or raw.get("llm_base_url") or "").rstrip("/"),
        llm_model=os.getenv("BOT_LLM_MODEL") or raw.get("llm_model") or "gpt-5.4-mini",
        vision_model=os.getenv("BOT_VISION_MODEL") or raw.get("vision_model") or "gpt-5.4",
        reply_model=os.getenv("BOT_REPLY_MODEL") or raw.get("reply_model") or os.getenv("BOT_LLM_MODEL") or raw.get("llm_model") or "gpt-5.4-mini",
        llm_api_key=os.getenv("BOT_LLM_API_KEY") or raw.get("llm_api_key") or "",
        poll_interval_sec=float(raw.get("poll_interval_sec", 2.0)),
        stable_seen_required=int(raw.get("stable_seen_required", 2)),
        min_event_confidence=float(raw.get("min_event_confidence", 0.65)),
        message_debounce_sec=float(raw.get("message_debounce_sec", 2.0)),
        chat_crop=CropConfig(
            left_ratio=float(crop.get("left_ratio", 0.0)),
            top_ratio=float(crop.get("top_ratio", 0.10)),
            right_ratio=float(crop.get("right_ratio", 1.0)),
            bottom_ratio=float(crop.get("bottom_ratio", 0.83)),
        ),
        bottom_scan_ratio=float(raw.get("bottom_scan_ratio", 0.45)),
        save_screenshots=bool(raw.get("save_screenshots", True)),
        activate_before_capture=bool(raw.get("activate_before_capture", True)),
        suggest_replies=bool(raw.get("suggest_replies", False)),
        admin_confirm_required=bool(raw.get("admin_confirm_required", True)),
        max_recent_messages=int(raw.get("max_recent_messages", 12)),
        skill_path=skill_path,
        data_dir=data_dir,
        log_dir=log_dir,
    )
