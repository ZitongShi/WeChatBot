from __future__ import annotations

from dataclasses import dataclass

import win32con
import win32gui
import win32process


WECHAT_PROCESS_HINTS = ("Weixin.exe", "WeChat.exe", "WeChatAppEx.exe")
WECHAT_CLASS_HINTS = ("Qt", "WeChat", "Weixin")


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    class_name: str
    title: str
    rect: tuple[int, int, int, int]
    width: int
    height: int
    visible: bool
    minimized: bool


def _window_info(hwnd: int) -> WindowInfo:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        class_name=win32gui.GetClassName(hwnd),
        title=win32gui.GetWindowText(hwnd),
        rect=rect,
        width=max(0, right - left),
        height=max(0, bottom - top),
        visible=bool(win32gui.IsWindowVisible(hwnd)),
        minimized=bool(win32gui.IsIconic(hwnd)),
    )


def list_windows() -> list[WindowInfo]:
    results: list[WindowInfo] = []

    def callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            info = _window_info(hwnd)
        except Exception:
            return
        if info.width < 200 or info.height < 200:
            return
        title_class = f"{info.title} {info.class_name}"
        if "微信" in title_class or any(hint in title_class for hint in WECHAT_CLASS_HINTS):
            results.append(info)

    win32gui.EnumWindows(callback, None)
    return results


def get_window(hwnd: int) -> WindowInfo:
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"window does not exist: {hwnd}")
    return _window_info(hwnd)


def ensure_window_ready(hwnd: int) -> WindowInfo:
    info = get_window(hwnd)
    if not info.visible:
        raise RuntimeError(f"window is not visible: {hwnd}")
    if info.minimized:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        info = get_window(hwnd)
    return info


def activate_window(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    except Exception:
        pass


def foreground_hwnd() -> int:
    return int(win32gui.GetForegroundWindow())
