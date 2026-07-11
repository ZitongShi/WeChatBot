from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from .capture import grab_window_chat, image_sha1, save_screen
from .config import load_config
from .dedupe import MessageDeduper
from .events import ChatEvent, compact_events
from .log import JsonlLogger
from .reply import ReplyAdvisor
from .sender import send_text_to_window
from .vision_ocr import VisionOcr
from .windows import activate_window, ensure_window_ready, get_window, list_windows


@dataclass
class WatchState:
    deduper: MessageDeduper
    history: list[ChatEvent] = field(default_factory=list)
    last_hash: str = ""
    last_new_at: float = 0.0
    pending: list[ChatEvent] = field(default_factory=list)


def cmd_list_windows(args: argparse.Namespace) -> int:
    windows = list_windows()
    if not windows:
        print("未找到可见微信窗口。")
        return 1
    for info in windows:
        print(
            f"hwnd={info.hwnd} pid={info.pid} class={info.class_name} "
            f"title={info.title!r} rect={info.rect} size={info.width}x{info.height}"
        )
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    info = ensure_window_ready(args.hwnd)
    warn_if_main_window(info.title)
    if config.activate_before_capture:
        activate_window(args.hwnd)
        time.sleep(0.3)
        info = ensure_window_ready(args.hwnd)
    image = grab_window_chat(info, config)
    path = save_screen(image, config.data_dir / "screens", f"snapshot-{args.hwnd}")
    print(f"已保存截图：{path}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    info = ensure_window_ready(args.hwnd)
    warn_if_main_window(info.title)
    if config.activate_before_capture:
        activate_window(args.hwnd)
        time.sleep(0.3)
        info = ensure_window_ready(args.hwnd)
    image = grab_window_chat(info, config)
    if config.save_screenshots:
        path = save_screen(image, config.data_dir / "screens", f"read-{args.hwnd}")
        print(f"截图：{path}")
    events = VisionOcr(config).extract_events(image, info.title)
    if not events:
        print("未识别到消息事件。")
        return 1
    for event in events:
        print_event("OCR", event)
    if args.suggest:
        customer_events = [
            event for event in events if event.is_customer_observable and event.confidence >= config.min_event_confidence
        ]
        if not customer_events:
            print("没有可用于建议回复的客户消息。")
            return 0
        decision = ReplyAdvisor(config).decide(customer_events, customer_events, info.title)
        print("\n建议：")
        print(f"action={decision.action} reason={decision.reason}")
        if decision.reply:
            print(decision.reply)
        if decision.customer_ack:
            print(f"ack={decision.customer_ack}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    suggest = bool(args.suggest or config.suggest_replies)
    auto_send = bool(args.auto_send)
    logger = JsonlLogger(config.log_dir / "events.jsonl")
    ocr = VisionOcr(config)
    advisor = ReplyAdvisor(config)
    deduper = MessageDeduper(config.stable_seen_required, config.min_event_confidence)
    history: list[ChatEvent] = []
    last_hash = ""
    last_new_at = 0.0
    pending: list[ChatEvent] = []

    initial_info = ensure_window_ready(args.hwnd)
    warn_if_main_window(initial_info.title)
    if auto_send and not suggest:
        suggest = True
    print("开始监听微信窗口。")
    print(f"hwnd={args.hwnd} suggest={suggest} auto_send={auto_send} interval={config.poll_interval_sec}s")

    while True:
        try:
            info = ensure_window_ready(args.hwnd)
            if config.activate_before_capture:
                activate_window(args.hwnd)
                time.sleep(0.2)
                info = ensure_window_ready(args.hwnd)
            image = grab_window_chat(info, config)
            digest = image_sha1(image)
            if digest == last_hash:
                time.sleep(config.poll_interval_sec)
                continue
            last_hash = digest

            screen_path = ""
            if config.save_screenshots:
                screen_path = str(save_screen(image, config.data_dir / "screens", f"watch-{args.hwnd}"))

            events = ocr.extract_events(image, info.title)
            accepted = deduper.accept(events)
            if accepted:
                pending.extend(accepted)
                last_new_at = time.time()
                for event in accepted:
                    print_event("NEW", event)
                    logger.write(
                        "message_detected",
                        hwnd=args.hwnd,
                        title=info.title,
                        role=event.role,
                        type=event.type,
                        text=event.normalized_text,
                        confidence=event.confidence,
                        screenshot=screen_path,
                    )

            if pending and time.time() - last_new_at >= config.message_debounce_sec:
                batch = pending
                pending = []
                history.extend(batch)
                print("\n合并消息：")
                print(compact_events(batch))
                logger.write(
                    "message_batch",
                    hwnd=args.hwnd,
                    title=info.title,
                    messages=[
                        {"role": e.role, "type": e.type, "text": e.normalized_text, "confidence": e.confidence}
                        for e in batch
                    ],
                )
                if suggest:
                    decision = advisor.decide(history, batch, info.title)
                    print("\n建议：")
                    print(f"action={decision.action} reason={decision.reason}")
                    if decision.reply:
                        print(decision.reply)
                    if decision.customer_ack:
                        print(f"ack={decision.customer_ack}")
                    logger.write(
                        "reply_suggested",
                        hwnd=args.hwnd,
                        title=info.title,
                        action=decision.action,
                        reason=decision.reason,
                        reply=decision.reply,
                        customer_ack=decision.customer_ack,
                    )
                    if auto_send and decision.action == "suggest" and decision.reply:
                        send_text_to_window(info, decision.reply, args.input_y_ratio)
                        history.append(ChatEvent(role="self", type="text", text=decision.reply, confidence=1.0))
                        logger.write(
                            "reply_auto_sent",
                            hwnd=args.hwnd,
                            title=info.title,
                            reply=decision.reply,
                        )
                        print("已自动发送。")
            time.sleep(config.poll_interval_sec)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0
        except Exception as exc:
            logger.write("watch_error", hwnd=args.hwnd, error=str(exc))
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 错误：{exc}", file=sys.stderr)
            time.sleep(max(config.poll_interval_sec, 3.0))


def cmd_watch_many(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    suggest = bool(args.suggest or args.auto_send or config.suggest_replies)
    auto_send = bool(args.auto_send)
    logger = JsonlLogger(config.log_dir / "events.jsonl")
    ocr = VisionOcr(config)
    advisor = ReplyAdvisor(config)
    states = {
        hwnd: WatchState(MessageDeduper(config.stable_seen_required, config.min_event_confidence))
        for hwnd in args.hwnd
    }

    for hwnd in args.hwnd:
        info = ensure_window_ready(hwnd)
        warn_if_main_window(info.title)
        print(f"监听窗口 hwnd={hwnd} title={info.title!r}")
    print(f"开始串行监听 {len(args.hwnd)} 个微信窗口。suggest={suggest} auto_send={auto_send} interval={config.poll_interval_sec}s")

    while True:
        try:
            for hwnd in args.hwnd:
                state = states[hwnd]
                info = ensure_window_ready(hwnd)
                if config.activate_before_capture:
                    activate_window(hwnd)
                    time.sleep(0.2)
                    info = ensure_window_ready(hwnd)
                image = grab_window_chat(info, config)
                digest = image_sha1(image)
                if digest != state.last_hash:
                    state.last_hash = digest
                    screen_path = ""
                    if config.save_screenshots:
                        screen_path = str(save_screen(image, config.data_dir / "screens", f"watch-{hwnd}"))
                    events = ocr.extract_events(image, info.title)
                    accepted = state.deduper.accept(events)
                    if accepted:
                        state.pending.extend(accepted)
                        state.last_new_at = time.time()
                        for event in accepted:
                            print_event(f"NEW[{info.title}]", event)
                            logger.write(
                                "message_detected",
                                hwnd=hwnd,
                                title=info.title,
                                role=event.role,
                                type=event.type,
                                text=event.normalized_text,
                                confidence=event.confidence,
                                screenshot=screen_path,
                            )

                if state.pending and time.time() - state.last_new_at >= config.message_debounce_sec:
                    batch = state.pending
                    state.pending = []
                    state.history.extend(batch)
                    print(f"\n合并消息[{info.title}]：")
                    print(compact_events(batch))
                    logger.write(
                        "message_batch",
                        hwnd=hwnd,
                        title=info.title,
                        messages=[
                            {"role": e.role, "type": e.type, "text": e.normalized_text, "confidence": e.confidence}
                            for e in batch
                        ],
                    )
                    if suggest:
                        decision = advisor.decide(state.history, batch, info.title)
                        print(f"\n建议[{info.title}]：")
                        print(f"action={decision.action} reason={decision.reason}")
                        if decision.reply:
                            print(decision.reply)
                        if decision.customer_ack:
                            print(f"ack={decision.customer_ack}")
                        logger.write(
                            "reply_suggested",
                            hwnd=hwnd,
                            title=info.title,
                            action=decision.action,
                            reason=decision.reason,
                            reply=decision.reply,
                            customer_ack=decision.customer_ack,
                        )
                        if auto_send and decision.action == "suggest" and decision.reply:
                            send_text_to_window(info, decision.reply, args.input_y_ratio)
                            state.history.append(ChatEvent(role="self", type="text", text=decision.reply, confidence=1.0))
                            logger.write(
                                "reply_auto_sent",
                                hwnd=hwnd,
                                title=info.title,
                                reply=decision.reply,
                            )
                            print(f"已自动发送[{info.title}]。")
            time.sleep(config.poll_interval_sec)
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0
        except Exception as exc:
            logger.write("watch_many_error", error=str(exc))
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 错误：{exc}", file=sys.stderr)
            time.sleep(max(config.poll_interval_sec, 3.0))


def cmd_send(args: argparse.Namespace) -> int:
    info = ensure_window_ready(args.hwnd)
    warn_if_main_window(info.title)
    if args.dry_run:
        print(f"dry-run: 将发送到 hwnd={args.hwnd} title={info.title!r}: {args.text}")
        return 0
    send_text_to_window(info, args.text, args.input_y_ratio)
    print(f"已发送到 hwnd={args.hwnd} title={info.title!r}: {args.text}")
    return 0


def print_event(prefix: str, event: ChatEvent) -> None:
    text = event.normalized_text or f"[{event.type}]"
    print(f"{prefix} role={event.role} type={event.type} conf={event.confidence:.2f} text={text}")


def warn_if_main_window(title: str) -> None:
    if title.strip() == "微信":
        print("警告：当前看起来是微信主窗口。第一版建议使用独立私聊窗口。", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wechat-chat-proxy")
    parser.add_argument("--config", default=None, help="config.json path")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list-windows", help="list visible WeChat-like windows")
    list_cmd.set_defaults(func=cmd_list_windows)

    snap_cmd = sub.add_parser("snapshot", help="save one chat-area screenshot")
    snap_cmd.add_argument("--hwnd", type=int, required=True)
    snap_cmd.set_defaults(func=cmd_snapshot)

    read_cmd = sub.add_parser("read", help="OCR one chat-area screenshot")
    read_cmd.add_argument("--hwnd", type=int, required=True)
    read_cmd.add_argument("--suggest", action="store_true", help="generate a suggested reply from this OCR result")
    read_cmd.set_defaults(func=cmd_read)

    watch_cmd = sub.add_parser("watch", help="watch one independent chat window")
    watch_cmd.add_argument("--hwnd", type=int, required=True)
    watch_cmd.add_argument("--suggest", action="store_true", help="generate suggested replies")
    watch_cmd.add_argument("--auto-send", action="store_true", help="send suggest replies automatically")
    watch_cmd.add_argument("--input-y-ratio", type=float, default=0.92)
    watch_cmd.set_defaults(func=cmd_watch)

    watch_many_cmd = sub.add_parser("watch-many", help="watch multiple independent chat windows serially")
    watch_many_cmd.add_argument("--hwnd", type=int, action="append", required=True)
    watch_many_cmd.add_argument("--suggest", action="store_true", help="generate suggested replies")
    watch_many_cmd.add_argument("--auto-send", action="store_true", help="send suggest replies automatically")
    watch_many_cmd.add_argument("--input-y-ratio", type=float, default=0.92)
    watch_many_cmd.set_defaults(func=cmd_watch_many)

    send_cmd = sub.add_parser("send", help="manually send text to a specified WeChat window")
    send_cmd.add_argument("--hwnd", type=int, required=True)
    send_cmd.add_argument("--text", required=True)
    send_cmd.add_argument("--input-y-ratio", type=float, default=0.92)
    send_cmd.add_argument("--dry-run", action="store_true")
    send_cmd.set_defaults(func=cmd_send)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
