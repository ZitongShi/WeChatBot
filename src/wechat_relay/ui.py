from __future__ import annotations

import difflib
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from .layout import SELECTED_ROW, Layout, align_rows, close_color, find_list_rows
from .screen import OcrLine, capture_screen, capture_window, ocr_lines, window_rect
from .vision import Row, VisionReader, normalize_for_match


class UiError(RuntimeError):
    pass


class WrongChat(UiError):
    """A different chat than wanted got opened. Opening it cleared its unread badge, so the
    caller must read it now or its new messages are never seen."""

    def __init__(self, message: str, title: str):
        super().__init__(message)
        self.title = title

# Set by the relay when debug screens are on: called as DEBUG_HOOK(step, details) at each
# navigation step so failures can be replayed from screenshots instead of guessed.
DEBUG_HOOK = None


def _debug(step: str, **details) -> None:
    if DEBUG_HOOK is not None:
        try:
            DEBUG_HOOK(step, details)
        except Exception:
            pass


def activate(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() == hwnd:
        return
    # SetForegroundWindow is refused unless this process recently had input; a stray
    # ALT press satisfies the foreground lock rules.
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    # The first click right after activation is otherwise swallowed by the activation.
    time.sleep(0.6)
    if win32gui.GetForegroundWindow() != hwnd:
        raise UiError("无法把微信窗口切到前台")


def wechat_windows(hwnd: int) -> dict[int, str]:
    """Visible top-level windows of the WeChat process other than the main window."""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    found: dict[int, str] = {}

    def callback(other: int, _: object) -> None:
        if other != hwnd and win32gui.IsWindowVisible(other):
            if win32process.GetWindowThreadProcessId(other)[1] == pid:
                found[other] = win32gui.GetClassName(other)

    win32gui.EnumWindows(callback, None)
    return found


def close_chat_popouts(hwnd: int) -> int:
    """Close chats popped out into their own windows (same window class as the main window)."""
    closed = 0
    for other, cls in wechat_windows(hwnd).items():
        if cls.endswith("QWindowIcon"):
            win32gui.PostMessage(other, win32con.WM_CLOSE, 0, 0)
            closed += 1
    if closed:
        time.sleep(0.6)
    return closed


def ensure_foreground(hwnd: int) -> None:
    foreground = win32gui.GetForegroundWindow()
    if foreground == hwnd:
        return
    # A WeChat menu / search popup in front of the main window is fine.
    if wechat_windows(hwnd).get(foreground, "").endswith("ToolSaveBits"):
        return
    activate(hwnd)


def to_screen(hwnd: int, point: tuple[int, int]) -> tuple[int, int]:
    left, top, _, _ = window_rect(hwnd)
    return left + point[0], top + point[1]


def _place_cursor(x: int, y: int) -> None:
    """Move the cursor and make sure it stays there.

    In a VM with mouse integration the host pointer keeps overwriting the guest cursor, so a
    click could land wherever the person's real mouse is. Refuse to click in that case.
    """
    for _ in range(3):
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        cx, cy = win32api.GetCursorPos()
        if abs(cx - x) <= 2 and abs(cy - y) <= 2:
            return
        time.sleep(0.2)
    raise UiError("鼠标位置被外部改动（虚拟机的鼠标集成？），已放弃点击")


def _press_button(right: bool = False) -> None:
    down, up = (
        (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)
        if right
        else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP)
    )
    win32api.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(up, 0, 0, 0, 0)


def click(hwnd: int, point: tuple[int, int], right: bool = False) -> None:
    ensure_foreground(hwnd)
    _place_cursor(*to_screen(hwnd, point))
    _press_button(right)


def click_screen(x: int, y: int) -> None:
    _place_cursor(x, y)
    _press_button()


def press(*keys: int) -> None:
    for key in keys:
        win32api.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


def set_clipboard_text(text: str) -> None:
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.1)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return
        finally:
            win32clipboard.CloseClipboard()
    raise UiError("剪贴板被占用")


def grab_clipboard_image():
    from PIL import Image, ImageGrab

    for _ in range(10):
        try:
            value = ImageGrab.grabclipboard()
        except Exception:
            value = None
        if isinstance(value, Image.Image):
            return value.convert("RGB")
        time.sleep(0.15)
    return None


def set_clipboard_image(image) -> None:
    import io

    buf = io.BytesIO()
    image.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]  # CF_DIB is a BMP without the file header
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            time.sleep(0.1)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
            return
        finally:
            win32clipboard.CloseClipboard()
    raise UiError("剪贴板被占用")


def names_match(expected: str, actual: str) -> bool:
    a = normalize_for_match(expected)
    b = normalize_for_match(actual)
    if not a or not b:
        return False
    if a in b or (len(b) >= 3 and b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


def _grab(hwnd: int, layout: Layout):
    """Capture the window, refusing a capture whose size no longer matches the layout
    (minimized or resized WeChat): its coordinates would be meaningless."""
    want = (layout.width, layout.height)
    image = capture_window(hwnd)
    if image.size != want:
        activate(hwnd)
        image = capture_window(hwnd)
        if image.size != want:
            raise UiError(f"微信窗口尺寸变了（{image.size[0]}x{image.size[1]}），需要重新识别布局")
    return image


def same_name(expected: str, shown: str) -> bool:
    """Strict match for names read by the vision model; a cut-off name ("ab...") matches its prefix."""
    a = normalize_for_match(expected)
    b = normalize_for_match(shown)
    if not a or not b:
        return False
    if a == b:
        return True
    stripped = shown.strip().rstrip(".…").strip()
    prefix = normalize_for_match(stripped)
    return stripped != shown.strip() and len(prefix) >= 2 and a.startswith(prefix)


SEARCH_ALLOWED = ("联系人", "群聊", "功能", "最常使用")


def pick_row(rows: list[Row], name: str, sections: tuple[str, ...] | None = None) -> Row | None:
    """The first row titled `name`; with `sections`, only rows under one of those headers."""
    for row in rows:
        if sections is not None and not any(row.section.startswith(h) for h in sections):
            continue
        if same_name(name, row.name):
            return row
    return None


def read_title(hwnd: int, layout: Layout, reader: VisionReader) -> tuple[str, int | None]:
    return reader.read_title(_grab(hwnd, layout).crop(layout.header_box))


def _verify_title(hwnd: int, layout: Layout, reader: VisionReader, name: str, how: str) -> str:
    title, count = read_title(hwnd, layout, reader)
    _debug("title_check", name=name, title=title, count=count)
    if not same_name(name, title):
        message = f"{how}失败：想打开 {name!r}，实际标题 {title!r}"
        if title:
            raise WrongChat(message, f"{title}({count})" if count else title)
        raise UiError(message)
    return f"{title}({count})" if count else title


def _input_region(layout: Layout) -> tuple[int, int, int, int]:
    return (layout.chat_left + 20, layout.input_top + 8, layout.chat_right - 20, layout.height - 70)


def _changed_pixels(a, b) -> int:
    from PIL import ImageChops

    diff = ImageChops.difference(a.convert("L"), b.convert("L")).point(lambda v: 255 if v > 40 else 0)
    return sum(1 for v in diff.getdata() if v)


def _search_popup(hwnd: int, layout: Layout, before: set[int]) -> tuple[int, tuple[int, int, int, int]] | None:
    """The search-result popup: a new WeChat window just below the search box, over the session list."""
    left, top, _, _ = window_rect(hwnd)
    sx, sy = left + layout.search_point[0], top + layout.search_point[1]
    for other in wechat_windows(hwnd):
        if other in before:
            continue
        box = win32gui.GetWindowRect(other)
        width, height = box[2] - box[0], box[3] - box[1]
        if height >= 150 and width >= 150 and box[0] <= sx <= box[2] and sy - 80 <= box[1] <= sy + 120:
            return other, box
    return None


def _row_selected(hwnd: int, layout: Layout, y: int) -> bool:
    """The clicked session-list row turns green once its chat is open."""
    image = _grab(hwnd, layout).convert("RGB")
    x = layout.list_left + 8
    return any(close_color(image.getpixel((x, yy)), SELECTED_ROW, 12) for yy in (y - 10, y, y + 10))


def open_chat_from_list(hwnd: int, layout: Layout, reader: VisionReader, name: str, on_leave=None) -> str | None:
    """Click the chat's row in the visible session list; None when it is not visible."""
    box = (layout.list_left, layout.header_bottom, layout.list_right, layout.height - 10)
    for attempt in range(3):
        image = _grab(hwnd, layout)
        names = reader.session_names(image.crop(box))
        if not names:
            # An empty answer is a failed read (the list is never empty): ask again.
            _debug("list_rows_empty", attempt=attempt)
            continue
        index = next((i for i, shown in enumerate(names) if same_name(name, shown)), None)
        avatars = find_list_rows(image, layout)
        aligned = align_rows(len(names), avatars, layout.header_bottom, box[3])
        y = aligned[index] if aligned and index is not None else None
        _debug("list_rows", name=name, names=names, index=index, avatars=avatars, y=y)
        # Reading the list takes seconds; a message arriving meanwhile moves its chat to the
        # top and shifts every row below, so only click a list that still looks the same.
        latest = _grab(hwnd, layout)
        if _changed_pixels(image.crop(box), latest.crop(box)) <= 50:
            break
        _debug("list_changed", attempt=attempt)
    else:
        raise UiError("会话列表读不出来或一直在变动，稍后再试")
    if y is None:
        return None
    point = ((layout.list_left + layout.list_right) // 2, y)
    if on_leave is not None:
        # The chat being left, as it looks right before it stops being the open one.
        on_leave(latest)
    _debug("list_click", point=point, screen=to_screen(hwnd, point))
    click(hwnd, point)
    time.sleep(1.0)
    _debug("after_list_click")
    if not _row_selected(hwnd, layout, point[1]) and not close_chat_popouts(hwnd):
        # The click did nothing (e.g. swallowed by window activation): try the same row once more.
        click(hwnd, point)
        time.sleep(1.0)
        _debug("after_list_click_retry")
    if close_chat_popouts(hwnd):
        raise UiError(f"{name!r} 被弹出成独立窗口，已关闭")
    return _verify_title(hwnd, layout, reader, name, "点击会话列表")


def open_chat(hwnd: int, layout: Layout, reader: VisionReader, name: str, on_leave=None) -> str:
    """Open a chat: click its row in the session list, else fall back to searching.

    Returns the header title ("群名(32)" for groups); raises unless it is exactly the name.
    """
    close_chat_popouts(hwnd)
    ensure_foreground(hwnd)
    title = open_chat_from_list(hwnd, layout, reader, name, on_leave)
    if title is not None:
        return title
    return open_chat_by_search(hwnd, layout, reader, name, on_leave)


def open_chat_by_search(hwnd: int, layout: Layout, reader: VisionReader, name: str, on_leave=None) -> str:
    """Open a chat via the search box; returns the header title, raises if it does not match.

    Guards against typing into the current chat: the result popup must sit right under the
    search box, the chat input is compared before and after pasting (and cleared if the
    paste landed there), and the clicked result must be inside the popup. Never presses Enter.
    """
    close_chat_popouts(hwnd)
    ensure_foreground(hwnd)
    if wechat_windows(hwnd):
        dismiss_popups(hwnd, layout)
    region = _input_region(layout)
    input_before = _grab(hwnd, layout).crop(region)
    before = set(wechat_windows(hwnd))
    _debug("search_click", point=layout.search_point, screen=to_screen(hwnd, layout.search_point))
    click(hwnd, layout.search_point)
    time.sleep(0.6)
    _debug("after_search_click")
    set_clipboard_text(name)
    press(win32con.VK_CONTROL, ord("A"))
    press(win32con.VK_CONTROL, ord("V"))
    time.sleep(1.5)
    if _changed_pixels(input_before, _grab(hwnd, layout).crop(region)) > 200:
        # The paste went into the chat input box: wipe it before anything can send it.
        click(hwnd, layout.input_point)
        press(win32con.VK_CONTROL, ord("A"))
        press(win32con.VK_DELETE)
        raise UiError(f"搜索 {name!r} 时文字落进了聊天输入框，已清空并中止")
    popup = _search_popup(hwnd, layout, before)
    if popup is None:
        dismiss_popups(hwnd, layout)
        raise UiError("没有找到搜索结果弹窗")
    _, box = popup
    rows = reader.search_rows(capture_screen(box))
    # Only contacts / groups: a 聊天记录 hit opens a separate window, 搜索网络结果 a web page.
    result = pick_row(rows, name, SEARCH_ALLOWED)
    _debug("search_rows", box=box, rows=[(r.section, r.name, *r.center) for r in rows], result=result and result.name)
    if result is None:
        dismiss_popups(hwnd, layout)
        raise UiError(f"搜索结果里没有找到 {name!r}")
    cx, cy = result.center
    if not (0 <= cx <= box[2] - box[0] and 0 <= cy <= box[3] - box[1]):
        dismiss_popups(hwnd, layout)
        raise UiError("搜索结果位置异常")
    if on_leave is not None:
        on_leave(_grab(hwnd, layout))  # the popup only covers the session list
    _debug("result_click", screen=(box[0] + cx, box[1] + cy))
    click_screen(box[0] + cx, box[1] + cy)
    time.sleep(1.2)
    _debug("after_result_click", popouts=list(wechat_windows(hwnd).values()))
    if close_chat_popouts(hwnd):
        raise UiError(f"{name!r} 被弹出成独立窗口，已关闭")
    ensure_foreground(hwnd)
    return _verify_title(hwnd, layout, reader, name, "打开会话")


def strip_member_count(title: str) -> str:
    """Group headers look like '群名(32)'."""
    import re

    return re.sub(r"[(（]\s*\d+\s*[)）]\s*$", "", title).strip()


def send_text(hwnd: int, layout: Layout, text: str) -> None:
    if not text.strip():
        return
    click(hwnd, layout.input_point)
    time.sleep(0.15)
    press(win32con.VK_CONTROL, ord("A"))
    press(win32con.VK_DELETE)
    set_clipboard_text(text)
    press(win32con.VK_CONTROL, ord("V"))
    time.sleep(0.4)
    ensure_foreground(hwnd)
    press(win32con.VK_RETURN)
    time.sleep(0.6)


def send_image(hwnd: int, layout: Layout, image) -> None:
    click(hwnd, layout.input_point)
    time.sleep(0.15)
    press(win32con.VK_CONTROL, ord("A"))
    press(win32con.VK_DELETE)
    set_clipboard_image(image)
    press(win32con.VK_CONTROL, ord("V"))
    time.sleep(1.0)
    ensure_foreground(hwnd)
    press(win32con.VK_RETURN)
    time.sleep(0.8)


def context_menu(hwnd: int, layout: Layout, point: tuple[int, int], labels: tuple[str, ...]) -> bool:
    """Right-click a point in the window and click the first menu item matching a label."""
    click(hwnd, point, right=True)
    time.sleep(0.6)
    sx, sy = to_screen(hwnd, point)
    vx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    vy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    vw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    vh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    box = (max(vx, sx - 450), max(vy, sy - 800), min(vx + vw, sx + 450), min(vy + vh, sy + 800))
    lines: list[OcrLine] = ocr_lines(capture_screen(box))
    for label in labels:
        for line in lines:
            text = line.text.replace(" ", "")
            if text == label or (text.startswith(label) and len(text) <= len(label) + 3):
                cx, cy = line.center
                click_screen(box[0] + cx, box[1] + cy)
                time.sleep(0.4)
                return True
    dismiss_popups(hwnd, layout)
    return False


def dismiss_popups(hwnd: int, layout: Layout) -> None:
    """Close menus / search dropdowns by clicking empty chat background (Esc would hide WeChat)."""
    left, top, right, _ = layout.messages_box
    click(hwnd, ((left + right) // 2, top + 6))
    time.sleep(0.3)
