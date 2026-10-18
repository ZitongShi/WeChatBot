from __future__ import annotations

import hashlib
import re
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from wechat_customer_bot.log import JsonlLogger

from . import ui
from .config import RelayConfig
from .layout import Layout, detect_layout, find_unread_rows
from .screen import capture_window, find_main_window, place_window, window_rect
from .store import RelayState
from .vision import ChatMessage, ChatRead, VisionReader


TYPE_LABELS = {
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "file": "文件",
    "link": "链接",
    "sticker": "表情",
    "other": "消息",
}

REPLY_ID = re.compile(r"#\s*(\d{1,6})")
# Our own forward header ("#12【群】…") showing up as message text means it is a quote.
FORWARD_QUOTE = re.compile(r"#\s*\d{1,6}\s*【[私群]】")
HELP_TEXT = "\n".join(
    [
        "【转发助手】用法：",
        "1. 引用某条转发消息后直接输入内容 → 回复给对应会话",
        "2. #编号 内容 → 例如：#12 好的",
        "3. /截图 #编号 或 /截图 会话名 → 发来该会话当前的截图（也可以引用转发消息后发 /截图）",
        "4. /暂停 30 → 暂停 30 分钟（不碰鼠标键盘），到点自动恢复；不写分钟数默认 30",
        "5. /停止 → 关掉 bot，要在电脑上重新启动",
        "6. /list → 最近转发过的会话",
        "7. /help → 显示本说明",
    ]
)


def format_message_line(message: ChatMessage, is_group: bool) -> str:
    if message.type == "text":
        body = message.text
    elif message.type == "voice":
        body = f"[语音] {message.text}" if message.text else "[语音，未能转文字]"
    elif message.type in {"file", "link"}:
        body = f"[{TYPE_LABELS[message.type]}] {message.text}".strip()
    else:
        label = TYPE_LABELS.get(message.type, "消息")
        body = f"[{label}] {message.text}".strip() if message.text else f"[{label}]"
    if message.quote:
        body += f"\n    ↳ 引用：{message.quote}"
    if is_group:
        return f"{message.sender or '某人'}：{body}"
    return body


def format_forward(fid: int, chat: str, is_group: bool, messages: list[ChatMessage], complete: bool) -> str:
    kind = "群" if is_group else "私"
    lines = [f"#{fid}【{kind}】{chat}"]
    lines.extend(format_message_line(m, is_group) for m in messages)
    if not complete:
        lines.append("（可能还有更早的消息未显示）")
    return "\n".join(lines)


@dataclass
class Command:
    kind: str  # reply | shot | list | help | unknown
    target_id: int | None = None
    text: str = ""


# Exit code that tells start_relay.ps1 the bot was stopped on purpose: do not restart it.
EXIT_STOPPED = 3
PAUSE = re.compile(r"^/\s*(暂停|pause)\s*(\d{1,4})?\s*(分钟|min|m)?\s*$", flags=re.IGNORECASE)


class StopRequested(Exception):
    """/停止 from the main account."""


SHOT = re.compile(r"^/\s*(截图|截屏|shot|ss)\s*(.*)$", flags=re.IGNORECASE | re.DOTALL)


def parse_command(message: ChatMessage) -> Command:
    text = message.text.strip()
    if text.lower() in {"/list", "/ls"}:
        return Command("list")
    if text.lower() in {"/help", "/h", "help"}:
        return Command("help")
    if text.lower().replace(" ", "") in {"/停止", "/stop", "/quit", "/exit"}:
        return Command("stop")
    pause = PAUSE.match(text)
    if pause:
        return Command("pause", text=pause.group(2) or "30")
    shot = SHOT.match(text)
    if shot:
        rest = shot.group(2).strip()
        by_id = re.match(r"^#\s*(\d{1,6})$", rest)
        if by_id:
            return Command("shot", int(by_id.group(1)))
        if rest:
            return Command("shot", text=rest)
        quoted = REPLY_ID.search(message.quote) if message.quote else None
        return Command("shot", int(quoted.group(1)) if quoted else None)
    match = re.match(r"^#\s*(\d{1,6})\s*[:：,，]?\s*(.+)$", text, flags=re.DOTALL)
    if match:
        return Command("reply", int(match.group(1)), match.group(2).strip())
    if message.quote:
        quoted = REPLY_ID.search(message.quote)
        if quoted and text:
            return Command("reply", int(quoted.group(1)), text)
    return Command("unknown", text=text)


def attach_split_quotes(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Fold a quoted forward the vision model listed as its own message back into the reply."""
    merged: list[ChatMessage] = []
    for message in messages:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and FORWARD_QUOTE.search(message.text)
            and not prev.quote
            and prev.side == message.side
            and prev.type == "text"
        ):
            prev.quote = message.text
            continue
        merged.append(message)
    return merged


def image_hash(image: Image.Image) -> str:
    return hashlib.sha1(image.convert("RGB").tobytes()).hexdigest()


class Relay:
    def __init__(self, config: RelayConfig):
        self.config = config
        self.logger = JsonlLogger(config.log_file)
        self.state = RelayState.load(config.state_file)
        self.vision = VisionReader(config.llm_base_url, config.llm_api_key, config.vision_model)
        self.hwnd = find_main_window()
        self.layout: Layout | None = None
        if config.save_debug_screens:
            ui.DEBUG_HOOK = self.debug_step
        # Chats the bot typed into. Messages that arrive while a chat is open never get an
        # unread badge, so each one is re-checked when idle: chat name -> messages-area hash
        # of the last read ("" forces a read).
        self.revisit: dict[str, str] = {chat: "" for chat in self.state.retry}
        self.revisit_failures: dict[str, int] = {}
        # Commands read from the main chat, run one per tick: a command that arrives while
        # another runs waits here instead of being lost.
        self.commands: deque[tuple[str, ChatMessage]] = deque()
        # While paused (/暂停) the bot does not touch the mouse or keyboard until this time.
        self.paused_until: float | None = None
        # The chat believed to be open; only trusted after its title is checked again.
        self.current: str | None = None
        # Messages-area hash of the image behind the last read.
        self.read_hash = ""

    # ---- helpers -------------------------------------------------------------

    def log(self, event: str, **data) -> None:
        self.logger.write(event, **data)
        stamp = datetime.now().strftime("%H:%M:%S")
        details = " ".join(f"{k}={v!r}" for k, v in data.items())
        print(f"[{stamp}] {event} {details}"[:400], flush=True)

    def capture(self) -> Image.Image:
        # capture_window grabs the real screen, so WeChat must be on top.
        ui.ensure_foreground(self.hwnd)
        image = capture_window(self.hwnd)
        if (
            self.layout is None
            or not self.layout.chat_open
            or (self.layout.width, self.layout.height) != image.size
        ):
            self.layout = detect_layout(image)
            self.log("layout", layout=str(self.layout))
        return image

    def messages_crop(self) -> Image.Image:
        image = self.capture()
        assert self.layout is not None
        return image.crop(self.layout.messages_box)

    def debug_step(self, step: str, details: dict) -> None:
        """Log a navigation step and save what the screen looks like at that moment."""
        stamp = datetime.now().strftime("%H%M%S-%f")[:-3]
        self.logger.write("ui_step", step=step, shot=f"{stamp}-{step}.png", **details)
        capture_window(self.hwnd).save(self.config.debug_dir / f"{stamp}-{step}.png")

    def save_debug(self, image: Image.Image, name: str) -> None:
        if self.config.save_debug_screens:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            image.save(self.config.debug_dir / f"{stamp}-{name}.png")

    def read_current_chat(self) -> ChatRead:
        """Title and messages of the open chat, from one vision call."""
        image = self.capture()
        assert self.layout is not None
        crop = image.crop(self.layout.chat_box)
        self.read_hash = image_hash(image.crop(self.layout.messages_box))
        self.save_debug(crop, "chat")
        read = self.vision.read_chat(crop)
        read.messages = attach_split_quotes(read.messages)
        if read.title:
            self.current = f"{read.title}({read.member_count})" if read.member_count else read.title
        return read

    def messages_point(self, message: ChatMessage) -> tuple[int, int] | None:
        if not message.box or self.layout is None:
            return None
        x1, y1, x2, y2 = message.box
        left, top, _, _ = self.layout.chat_box
        return left + (x1 + x2) // 2, top + (y1 + y2) // 2

    def open_chat(self, name: str) -> str:
        # Re-capture first so a resized window never leaves us clicking stale coordinates.
        self.capture()
        assert self.layout is not None
        if self.current and ui.same_name(name, ui.strip_member_count(self.current)):
            # Already open (e.g. the next queued command): checking the title is enough.
            title, count = ui.read_title(self.hwnd, self.layout, self.vision)
            if ui.same_name(name, title):
                self.log("open_chat", name=name, already_open=True)
                return f"{title}({count})" if count else title
        self.current = None
        self.log("open_chat", name=name)
        try:
            self.current = ui.open_chat(self.hwnd, self.layout, self.vision, name)
            return self.current
        except ui.UiError as exc:
            if isinstance(exc, ui.WrongChat):
                # Opening it cleared its unread badge: read it on the next idle tick.
                self.current = exc.title
                opened = ui.strip_member_count(exc.title)
                self.revisit[opened] = ""
                self.state.mark_retry(opened)
                self.log("opened_wrong_chat", wanted=name, opened=opened)
            # Keep the scene of every failed navigation for debugging.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                capture_window(self.hwnd).save(self.config.debug_dir / f"{stamp}-open-failed.png")
            except Exception:
                pass
            raise

    def type_in(self, chat: str, text: str, images: list[Image.Image] | None = None) -> ChatRead:
        """Send into the open chat, then read it right away and relay what is new.

        Messages that arrived while the bot was busy are in this read; anything later
        changes the messages area, so the idle re-check (`revisit`) catches it.
        """
        assert self.layout is not None
        if text:
            ui.send_text(self.hwnd, self.layout, text)
        for image in images or []:
            ui.send_image(self.hwnd, self.layout, image)
        read = self.read_current_chat()
        self.revisit[chat] = self.read_hash
        self.process_read(read)
        return read

    def park(self) -> None:
        try:
            self.open_chat(self.config.park_chat)
        except ui.UiError as exc:
            self.log("park_failed", error=str(exc))

    def is_main_chat(self, chat: str) -> bool:
        return ui.names_match(self.config.forward_to_title, chat)

    # ---- main loop -----------------------------------------------------------

    def run(self) -> int:
        """Relay until stopped; returns the process exit code."""
        place_window(self.hwnd, self.config.window_rect)
        ui.activate(self.hwnd)
        self.wait_for_window_size()
        self.capture()
        try:
            self.bootstrap_main_chat()
        except Exception as exc:
            self.log("bootstrap_failed", error=str(exc), trace=traceback.format_exc()[-800:])
            raise
        self.park()
        self.log("started", forward_to=self.config.forward_to, park=self.config.park_chat)
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                self.log("stopped")
                return 0
            except StopRequested:
                self.log("stopped_by_command")
                return EXIT_STOPPED
            except Exception as exc:
                self.log("error", error=str(exc), trace=traceback.format_exc()[-800:])
                time.sleep(3)
                try:
                    self.layout = None
                    self.capture()
                    self.park()
                except Exception:
                    pass
            time.sleep(self.config.poll_interval_sec)

    def wait_for_window_size(self, timeout: float = 8.0) -> None:
        """WeChat applies a resize asynchronously; wait until the rect matches and settles."""
        want = self.config.window_rect
        if want is None:
            time.sleep(1.0)  # maximize is applied asynchronously too
            return
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            left, top, right, bottom = window_rect(self.hwnd)
            current = (left, top, right - left, bottom - top)
            if current == last and abs(current[2] - want[2]) <= 8 and abs(current[3] - want[3]) <= 8:
                return
            last = current
            time.sleep(0.5)
        self.log("window_size_mismatch", want=list(want), got=list(last or ()))

    def bootstrap_main_chat(self) -> None:
        """Remember what is already in the main chat so old messages are not run as commands."""
        chat = ui.strip_member_count(self.open_chat(self.config.forward_to))
        read = self.read_current_chat()
        self.state.take_new(chat, read.messages)
        self.state.save()
        self.log("bootstrap", chat=chat, visible=len(read.messages))

    def tick(self) -> None:
        if self.paused_until is not None:
            if time.time() < self.paused_until:
                return  # not even a screenshot: capturing brings WeChat to the front
            self.paused_until = None
            self.log("resumed")
            self.send_to_main(self.config.forward_to_title, "▶ 暂停结束，bot 已恢复")
            self.park_if_idle()
            return
        if self.commands:
            main_chat, message = self.commands.popleft()
            self.handle_command(main_chat, message)
            self.park_if_idle()
            return
        image = self.capture()
        assert self.layout is not None
        rows = find_unread_rows(image, self.layout)
        if rows:
            self.current = None
            ui.click(self.hwnd, rows[0].click_point)
            time.sleep(0.9)
            self.handle_open_chat()
            self.park_if_idle()
            return
        if self.revisit:
            chat, digest = next(iter(self.revisit.items()))
            del self.revisit[chat]
            try:
                self.open_chat(chat)
            except ui.UiError as exc:
                self.log("revisit_failed", chat=chat, error=str(exc))
                failures = self.revisit_failures.get(chat, 0) + 1
                self.revisit_failures[chat] = failures
                if chat in self.state.retry:
                    if failures < 3:
                        self.revisit[chat] = ""  # unrelayed messages: try again later
                    else:
                        self.state.clear_retry(chat)
                        self.log("retry_given_up", chat=chat)
                # Never stay in some other chat: messages arriving there get no badge.
                self.park()
                return
            if image_hash(self.messages_crop()) != digest:
                self.handle_open_chat()
            self.revisit_failures.pop(chat, None)
            self.state.clear_retry(chat)
            self.park_if_idle()

    def park_if_idle(self) -> None:
        # With commands queued the bot stays put; the next one usually needs the main chat.
        if not self.commands:
            self.park()

    def handle_open_chat(self) -> None:
        read = self.read_current_chat()
        if read.messages and self.config.transcribe_voice and self.transcribe_voices(read.title, read):
            read = self.read_current_chat()
        self.process_read(read)

    def process_read(self, read: ChatRead) -> None:
        """Relay what is new in a read of the open chat (commands, if it is the main chat)."""
        chat = read.title or "未知会话"
        is_group = read.is_group
        if not read.messages:
            # A failed read must not replace the anchor.
            self.log("empty_read", chat=chat)
            return
        previous_anchor = list(self.state.anchors.get(chat, []))
        new, complete = self.state.take_new(chat, read.messages)
        self.state.save()
        incoming = [m for m in new if m.side == "L"]
        self.log("chat_read", chat=chat, group=is_group, visible=len(read.messages), new=len(incoming))
        if not incoming:
            return

        if self.is_main_chat(chat):
            self.commands.extend((chat, message) for message in incoming)
            self.log("commands_queued", count=len(incoming), pending=len(self.commands))
            return

        if any(ui.names_match(name, chat) for name in self.config.ignore_chats):
            return
        try:
            self.forward(chat, is_group, incoming, complete)
        except Exception:
            # Not relayed: forget that they were seen, and read the chat again when idle.
            self.state.anchors[chat] = previous_anchor
            self.state.mark_retry(chat)
            self.revisit[chat] = ""
            self.log("forward_failed_will_retry", chat=chat, count=len(incoming))
            raise

    def transcribe_voices(self, chat: str, read: ChatRead) -> bool:
        """Use WeChat's own 转文字 on new voice bubbles that have no transcript yet."""
        pending = [m for m in read.messages[-6:] if m.side == "L" and m.type == "voice" and not m.text]
        clicked = 0
        for message in pending:
            point = self.messages_point(message)
            if point and ui.context_menu(self.hwnd, self.layout, point, ("转文字",)):
                clicked += 1
        if clicked:
            time.sleep(3.0)
            self.log("voice_transcribe", chat=chat, count=clicked)
        return bool(clicked)

    def copy_images(self, messages: list[ChatMessage]) -> list[Image.Image]:
        """Copy original images via WeChat's 复制 menu while the source chat is open."""
        copied: list[Image.Image] = []
        for message in [m for m in messages if m.type == "image"][: self.config.max_images_per_batch]:
            point = self.messages_point(message)
            if not point or not ui.context_menu(self.hwnd, self.layout, point, ("复制",)):
                self.log("image_copy_failed")
                continue
            image = ui.grab_clipboard_image()
            if image is None:
                self.log("image_clipboard_empty")
                continue
            copied.append(image)
        return copied

    def forward(self, chat: str, is_group: bool, messages: list[ChatMessage], complete: bool) -> None:
        images = self.copy_images(messages) if self.config.forward_images else []
        fid = self.state.new_forward(chat, is_group)
        self.state.save()
        text = format_forward(fid, chat, is_group, messages, complete)

        main = ui.strip_member_count(self.open_chat(self.config.forward_to))
        self.log("forwarded", id=fid, chat=chat, count=len(messages), images=len(images))
        self.type_in(main, text, images)

    def handle_command(self, main_chat: str, message: ChatMessage) -> None:
        if message.type != "text":
            self.log("command_skipped_non_text", type=message.type, text=message.text[:40])
            return
        if FORWARD_QUOTE.search(message.text):
            # A quoted forward that the vision model split out of the replying bubble.
            self.log("command_skipped_quote", text=message.text[:60])
            return
        command = parse_command(message)
        self.log("command", kind=command.kind, target=command.target_id, text=command.text[:80])
        if command.kind == "help":
            self.send_to_main(main_chat, HELP_TEXT)
            return
        if command.kind == "stop":
            self.send_to_main(main_chat, "⏹ bot 已停止。要重新启动，请在电脑上运行 start_relay.ps1")
            raise StopRequested()
        if command.kind == "pause":
            minutes = max(1, min(int(command.text or 30), 24 * 60))
            self.paused_until = time.time() + minutes * 60
            until = datetime.fromtimestamp(self.paused_until)
            self.send_to_main(main_chat, f"⏸ 暂停 {minutes} 分钟，{until:%H:%M} 自动恢复。期间收到的消息恢复后再转发。")
            # Leave the main chat: messages there would get no badge while paused.
            self.park()
            self.log("paused", minutes=minutes)
            return
        if command.kind == "list":
            recent = self.state.recent_chats()
            lines = (["【最近会话】"] + [f"#{fid} {chat}" for fid, chat in recent]) if recent else ["暂无转发记录"]
            self.send_to_main(main_chat, "\n".join(lines))
            return
        if command.kind == "unknown":
            self.send_to_main(main_chat, "没看懂要回复给谁：请引用转发消息，或用“#编号 内容”。发 /help 看用法。")
            return

        if command.kind == "shot":
            self.send_screenshot(main_chat, command)
            return

        target = self.state.forward_target(command.target_id or 0)
        if not target:
            self.send_to_main(main_chat, f"找不到 #{command.target_id}，发 /list 看最近会话。")
            return
        try:
            self.open_chat(target)
            # type_in also relays what arrived there: opening the chat cleared its badge.
            read = self.type_in(target, command.text)
            ok = any(m.side == "R" and ui.names_match(command.text, m.text) for m in read.messages[-3:])
            ack = (
                f"✓ 已回复 #{command.target_id} {target}"
                if ok
                else f"? 已发给 #{command.target_id} {target}，但没确认到消息，请检查"
            )
            self.log("replied", id=command.target_id, chat=target, verified=ok)
        except ui.UiError as exc:
            ack = f"✗ 回复 #{command.target_id} 失败：{exc}"
            self.log("reply_failed", id=command.target_id, chat=target, error=str(exc))
        self.send_to_main(main_chat, ack)

    def send_to_main(self, main_chat: str, text: str, image: Image.Image | None = None) -> None:
        try:
            self.open_chat(self.config.forward_to)
            self.type_in(main_chat, text, [image] if image is not None else None)
        except ui.UiError as exc:
            self.log("ack_failed", error=str(exc))

    def send_screenshot(self, main_chat: str, command: Command) -> None:
        """Open a chat and send what it shows right now (title bar and messages) as an image."""
        if command.target_id is not None:
            target = self.state.forward_target(command.target_id)
            if not target:
                self.send_to_main(main_chat, f"找不到 #{command.target_id}，发 /list 看最近会话。")
                return
        elif command.text:
            target = command.text
        else:
            self.send_to_main(main_chat, "要截哪个会话？用“/截图 #编号”或“/截图 会话名”，或引用转发消息后发 /截图。")
            return
        try:
            title = ui.strip_member_count(self.open_chat(target))
            image = self.capture()
            assert self.layout is not None
            shot = image.crop(self.layout.chat_box)
            self.log("screenshot", chat=title, size=list(shot.size))
            self.process_read(self.read_current_chat())
        except ui.UiError as exc:
            self.log("screenshot_failed", chat=target, error=str(exc))
            self.send_to_main(main_chat, f"✗ 截图 {target} 失败：{exc}")
            return
        label = f"#{command.target_id} " if command.target_id is not None else ""
        self.send_to_main(main_chat, f"📷 {label}{title} 的截图（{datetime.now():%H:%M}）", shot)
