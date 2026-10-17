from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RelayConfig:
    forward_to: str
    forward_to_title: str
    park_chat: str
    poll_interval_sec: float
    llm_base_url: str
    llm_api_key: str
    vision_model: str
    forward_images: bool
    max_images_per_batch: int
    transcribe_voice: bool
    ignore_chats: list[str] = field(default_factory=list)
    window_rect: tuple[int, int, int, int] | None = None
    state_file: Path = ROOT / "data" / "relay" / "state.json"
    log_file: Path = ROOT / "logs" / "relay.jsonl"
    debug_dir: Path = ROOT / "data" / "relay" / "debug"
    save_debug_screens: bool = False


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _resolve(path_value: str | None, default: Path) -> Path:
    path = Path(path_value) if path_value else default
    return path if path.is_absolute() else ROOT / path


def load_relay_config(path: str | None = None) -> RelayConfig:
    example = _read_json(ROOT / "relay.example.json")
    user = _read_json(Path(path) if path else ROOT / "relay.json")
    raw = {**example, **user}

    env_file = _resolve(raw.get("soclaas_env_file"), ROOT / "data" / "relay" / "soclaas.env")
    # The project's env file wins: a stale SOCLAAS_API_KEY in the user environment
    # should not silently override the key configured for this bot.
    env = {**os.environ, **_read_env_file(env_file)}

    state_file = _resolve(raw.get("state_file"), ROOT / "data" / "relay" / "state.json")
    log_file = _resolve(raw.get("log_file"), ROOT / "logs" / "relay.jsonl")
    debug_dir = _resolve(raw.get("debug_dir"), ROOT / "data" / "relay" / "debug")
    for directory in (state_file.parent, log_file.parent, debug_dir):
        directory.mkdir(parents=True, exist_ok=True)

    forward_to = str(raw.get("forward_to") or "zitong")
    return RelayConfig(
        forward_to=forward_to,
        forward_to_title=str(raw.get("forward_to_title") or forward_to),
        park_chat=str(raw.get("park_chat") or "文件传输助手"),
        poll_interval_sec=float(raw.get("poll_interval_sec", 1.0)),
        llm_base_url=(env.get("SOCLAAS_BASE_URL") or str(raw.get("llm_base_url") or "")).rstrip("/"),
        llm_api_key=env.get("SOCLAAS_API_KEY") or "",
        vision_model=str(raw.get("vision_model") or "qwen3.6:35b"),
        forward_images=bool(raw.get("forward_images", True)),
        max_images_per_batch=int(raw.get("max_images_per_batch", 3)),
        transcribe_voice=bool(raw.get("transcribe_voice", True)),
        ignore_chats=[str(v) for v in raw.get("ignore_chats", [])],
        # null (the default) maximizes the WeChat window; [left, top, width, height] pins it.
        window_rect=tuple(int(v) for v in raw["window_rect"]) if raw.get("window_rect") else None,
        state_file=state_file,
        log_file=log_file,
        debug_dir=debug_dir,
        save_debug_screens=bool(raw.get("save_debug_screens", False)),
    )
