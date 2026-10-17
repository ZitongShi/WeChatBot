from __future__ import annotations

import argparse
import sys

from .screen import enable_dpi_awareness


def cmd_layout(args: argparse.Namespace) -> int:
    from PIL import ImageDraw

    from .config import load_relay_config
    from .layout import detect_layout, find_unread_rows
    from .screen import capture_window, find_main_window

    from . import ui

    config = load_relay_config(args.config)
    hwnd = find_main_window()
    ui.activate(hwnd)
    image = capture_window(hwnd)
    layout = detect_layout(image)
    rows = find_unread_rows(image, layout)
    print(layout)
    print(f"unread rows: {rows}")
    draw = ImageDraw.Draw(image)
    draw.rectangle(layout.header_box, outline=(0, 0, 255), width=3)
    draw.rectangle(layout.messages_box, outline=(0, 160, 0), width=3)
    draw.rectangle((layout.list_left, 0, layout.list_right, layout.height - 1), outline=(255, 160, 0), width=3)
    for row in rows:
        draw.rectangle(row.badge_box, outline=(255, 0, 255), width=3)
        x, y = row.click_point
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 0, 255))
    x, y = layout.input_point
    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(0, 0, 255))
    out = config.debug_dir / "layout.png"
    image.save(out)
    print(f"saved {out}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from .config import load_relay_config
    from .layout import detect_layout
    from .screen import capture_window, find_main_window
    from . import ui
    from .vision import VisionReader

    config = load_relay_config(args.config)
    hwnd = find_main_window()
    ui.activate(hwnd)
    image = capture_window(hwnd)
    layout = detect_layout(image)
    title = ui.read_header(hwnd, layout)
    print(f"title={title!r} group={ui.is_group_title(title)}")
    read = VisionReader(config.llm_base_url, config.llm_api_key, config.vision_model).read_chat(
        image.crop(layout.messages_box)
    )
    for m in read.messages:
        print(f"  {m.side} {m.type:<7} sender={m.sender!r} text={m.text!r} quote={m.quote!r} box={m.box}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .config import load_relay_config
    from .relay import Relay

    Relay(load_relay_config(args.config)).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    enable_dpi_awareness()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="wechat_relay", description="小号微信 → 大号 消息转发与回复")
    parser.add_argument("--config", default=None, help="relay.json path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("layout", help="save a screenshot annotated with detected regions").set_defaults(func=cmd_layout)
    sub.add_parser("probe", help="read the currently open chat (no sending)").set_defaults(func=cmd_probe)
    sub.add_parser("run", help="start relaying").set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
