from __future__ import annotations

import time

import win32api
import win32clipboard
import win32con

from .windows import WindowInfo, activate_window


def send_text_to_window(info: WindowInfo, text: str, input_y_ratio: float = 0.92) -> None:
    if not text.strip():
        raise ValueError("text is empty")
    activate_window(info.hwnd)
    time.sleep(0.3)
    click_input_area(info, input_y_ratio)
    time.sleep(0.1)
    with temporary_clipboard(text):
        press_ctrl_v()
        time.sleep(0.1)
        press_enter()


def click_input_area(info: WindowInfo, input_y_ratio: float) -> None:
    left, top, right, bottom = info.rect
    x = int(left + (right - left) * 0.50)
    y = int(top + (bottom - top) * input_y_ratio)
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


class temporary_clipboard:
    def __init__(self, text: str):
        self.text = text
        self.previous: str | None = None

    def __enter__(self) -> None:
        win32clipboard.OpenClipboard()
        try:
            try:
                self.previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            except Exception:
                self.previous = None
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, self.text)
        finally:
            win32clipboard.CloseClipboard()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.previous is None:
            return
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, self.previous)
        finally:
            win32clipboard.CloseClipboard()


def press_ctrl_v() -> None:
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, 0, 0)
    win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def press_enter() -> None:
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
