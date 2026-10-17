from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass

from PIL import Image, ImageGrab
import win32con
import win32gui


PW_RENDERFULLCONTENT = 2


def enable_dpi_awareness() -> None:
    """Make window rects, screenshots and mouse coordinates all use physical pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_main_window() -> int:
    """The WeChat 4.x main window is the Qt window titled exactly '微信'."""
    found: list[int] = []

    def callback(hwnd: int, _: object) -> None:
        if win32gui.GetWindowText(hwnd) == "微信" and win32gui.GetClassName(hwnd).endswith("QWindowIcon"):
            found.append(hwnd)

    win32gui.EnumWindows(callback, None)
    if not found:
        raise RuntimeError("未找到微信主窗口（标题为“微信”的 Qt 窗口），请先登录微信")
    hwnd = found[0]
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        # Hidden to the tray or minimized: bring it back, PrintWindow needs it rendered.
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    return hwnd


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    return win32gui.GetWindowRect(hwnd)


def place_window(hwnd: int, rect: tuple[int, int, int, int] | None) -> None:
    """Maximize WeChat (rect None) or pin it to a fixed geometry, so the layout is predictable."""
    if rect is None:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return
    left, top, width, height = rect
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
    win32gui.SetWindowPos(hwnd, 0, left, top, width, height, win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)


def capture_window(hwnd: int) -> Image.Image:
    """Capture what is actually on screen inside the window rect.

    PrintWindow returns a stale frame for WeChat 4 after it has been hidden to the tray,
    so the window must be visible and unobstructed (the relay keeps it in the foreground).
    """
    left, top, right, bottom = window_rect(hwnd)
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


def print_window(hwnd: int) -> Image.Image:
    """Background capture via PrintWindow; kept for debugging only (can return stale frames)."""
    # win32ui needs the MFC runtime, which a fresh Windows install does not have.
    import win32ui

    left, top, right, bottom = window_rect(hwnd)
    width, height = right - left, bottom - top
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)
    try:
        if not ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed")
        info = bitmap.GetInfo()
        return Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def capture_screen(box: tuple[int, int, int, int]) -> Image.Image:
    """Capture a screen region; used for popup menus, which are separate windows."""
    return ImageGrab.grab(bbox=box, all_screens=True)


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


_CJK_SPACE = re.compile(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])")


def ocr_lines(image: Image.Image) -> list[OcrLine]:
    """Windows built-in OCR (zh-Hans). Fast but noisy: only use it for short UI labels."""
    import winocr

    if image.width < 200 or image.height < 60:
        scale = 2
        image = image.resize((image.width * scale, image.height * scale))
    else:
        scale = 1
    result = winocr.recognize_pil_sync(image, "zh-Hans-CN")
    lines: list[OcrLine] = []
    for line in result.get("lines", []):
        words = line.get("words") or []
        if not words:
            continue
        xs = [w["bounding_rect"]["x"] for w in words]
        ys = [w["bounding_rect"]["y"] for w in words]
        x2 = [w["bounding_rect"]["x"] + w["bounding_rect"]["width"] for w in words]
        y2 = [w["bounding_rect"]["y"] + w["bounding_rect"]["height"] for w in words]
        text = _CJK_SPACE.sub("", str(line.get("text") or "")).strip()
        lines.append(
            OcrLine(
                text=text,
                x=int(min(xs) / scale),
                y=int(min(ys) / scale),
                width=int((max(x2) - min(xs)) / scale),
                height=int((max(y2) - min(ys)) / scale),
            )
        )
    return lines
