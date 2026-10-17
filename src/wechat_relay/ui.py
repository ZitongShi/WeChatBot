from __future__ import annotations

import difflib
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from .layout import Layout
from .screen import OcrLine, capture_screen, capture_window, ocr_lines, ocr_text, window_rect
from .vision import normalize_for_match


class UiError(RuntimeError):
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
    time.sleep(0.25)
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


def click(hwnd: int, point: tuple[int, int], right: bool = False) -> None:
    ensure_foreground(hwnd)
    x, y = to_screen(hwnd, point)
    win32api.SetCursorPos((x, y))
    down, up = (
        (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)
        if right
        else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP)
    )
    win32api.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(up, 0, 0, 0, 0)


def click_screen(x: int, y: int) -> None:
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


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


def read_header(hwnd: int, layout: Layout) -> str:
    import re

    title = ocr_text(capture_window(hwnd).crop(layout.header_box))
    # OCR sometimes adds stray brackets/punctuation before the title.
    return re.sub(r"^[^\w\[【(（]+", "", title).strip()


SEARCH_HEADERS = ("联系人", "群聊", "功能", "最常使用", "聊天记录", "聊天文件", "搜索网络结果", "更多", "查看全部")
SEARCH_ALLOWED = {"联系人", "群聊", "功能", "最常使用"}


def pick_search_result(lines: list[OcrLine], name: str) -> OcrLine | None:
    """Pick the contact/group result whose title is the name (never a chat-history hit)."""
    titles: list[OcrLine] = []
    section = "联系人"  # results start with contacts when the header is not recognised
    for line in sorted(lines, key=lambda l: l.y):
        text = line.text.replace(" ", "")
        header = next((h for h in SEARCH_HEADERS if text.startswith(h) and len(text) <= len(h) + 4), None)
        if header:
            section = header
            continue
        if section in SEARCH_ALLOWED and 80 <= line.x <= 200 and ":" not in text and "：" not in text:
            titles.append(line)
    target = normalize_for_match(name)
    for line in titles:
        if normalize_for_match(line.text) == target:
            return line
    # Windows OCR garbles some glyphs (件 -> 亻牛), so fall back to close matches.
    scored = [
        (difflib.SequenceMatcher(None, target, normalize_for_match(l.text)).ratio(), l) for l in titles
    ]
    best = max(scored, key=lambda item: item[0], default=None)
    if best and best[0] >= 0.75:
        return best[1]
    for line in titles:
        text = line.text.rstrip(".…")
        if line.text != text and len(normalize_for_match(text)) >= 2 and target.startswith(normalize_for_match(text)):
            return line
    return None


def open_chat(hwnd: int, layout: Layout, name: str) -> str:
    """Open a chat via the search box; returns the header title, raises if it does not match.

    Nothing is typed unless the search popup is confirmed open, and the result is clicked
    by its exact title, so a failure can never paste the name into the current chat.
    """
    close_chat_popouts(hwnd)
    ensure_foreground(hwnd)
    if wechat_windows(hwnd):
        dismiss_popups(hwnd, layout)
    before = set(wechat_windows(hwnd))
    click(hwnd, layout.search_point)
    time.sleep(0.6)
    if not [w for w in wechat_windows(hwnd) if w not in before]:
        raise UiError("搜索框没有打开")
    set_clipboard_text(name)
    press(win32con.VK_CONTROL, ord("A"))
    press(win32con.VK_CONTROL, ord("V"))
    time.sleep(1.5)
    popups = [w for w in wechat_windows(hwnd) if w not in before]
    if not popups:
        raise UiError("搜索结果没有出现")
    box = win32gui.GetWindowRect(popups[0])
    result = pick_search_result(ocr_lines(capture_screen(box)), name)
    if result is None:
        dismiss_popups(hwnd, layout)
        raise UiError(f"搜索结果里没有找到 {name!r}")
    cx, cy = result.center
    click_screen(box[0] + cx, box[1] + cy)
    time.sleep(1.2)
    if close_chat_popouts(hwnd):
        raise UiError(f"{name!r} 被弹出成独立窗口，已关闭")
    ensure_foreground(hwnd)
    title = read_header(hwnd, layout)
    if not names_match(name, strip_member_count(title)):
        raise UiError(f"打开会话失败：想打开 {name!r}，实际标题 {title!r}")
    return title


def strip_member_count(title: str) -> str:
    """Group headers look like '群名(32)'."""
    import re

    return re.sub(r"[(（]\s*\d+\s*[)）]\s*$", "", title).strip()


def is_group_title(title: str) -> bool:
    import re

    return bool(re.search(r"[(（]\s*\d+\s*[)）]\s*$", title))


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
