from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageGrab
import win32con
import win32gui
import win32ui

from .config import AppConfig, CropConfig
from .windows import WindowInfo


def crop_rect(rect: tuple[int, int, int, int], crop: CropConfig) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    return (
        int(left + width * crop.left_ratio),
        int(top + height * crop.top_ratio),
        int(left + width * crop.right_ratio),
        int(top + height * crop.bottom_ratio),
    )


def bottom_rect(rect: tuple[int, int, int, int], ratio: float) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    height = bottom - top
    scan_top = int(bottom - height * ratio)
    return (left, scan_top, right, bottom)


def grab_window_chat(info: WindowInfo, config: AppConfig) -> Image.Image:
    full = grab_window_image(info)
    width, height = full.size
    chat = crop_rect((0, 0, width, height), config.chat_crop)
    scan = bottom_rect(chat, config.bottom_scan_ratio)
    return full.crop(scan)


def grab_window_image(info: WindowInfo) -> Image.Image:
    try:
        return print_window_image(info.hwnd, info.width, info.height)
    except Exception:
        return ImageGrab.grab(bbox=info.rect, all_screens=True)


def print_window_image(hwnd: int, width: int, height: int) -> Image.Image:
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)
    try:
        result = win32gui.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 3)
        if result != 1:
            raise RuntimeError("PrintWindow returned 0")
        bmpinfo = bitmap.GetInfo()
        bmpstr = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        )
        return image
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def image_sha1(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    return hashlib.sha1(rgb.tobytes()).hexdigest()


def save_screen(image: Image.Image, directory: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = directory / f"{prefix}-{stamp}.png"
    image.save(path)
    return path
