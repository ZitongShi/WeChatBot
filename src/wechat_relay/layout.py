from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


# Colors measured on WeChat 4.1 (light theme).
SESSION_LIST_BG = (238, 238, 240)
CHAT_BG = (250, 250, 250)
BADGE_RED = (250, 81, 81)


@dataclass(frozen=True)
class Layout:
    """Regions of the WeChat main window, in window-image pixels."""

    width: int
    height: int
    list_left: int
    list_right: int
    chat_left: int
    chat_right: int
    header_top: int
    header_bottom: int
    input_top: int
    chat_open: bool = True

    @property
    def header_box(self) -> tuple[int, int, int, int]:
        return (self.chat_left, self.header_top, self.chat_right - 120, self.header_bottom)

    @property
    def messages_box(self) -> tuple[int, int, int, int]:
        return (self.chat_left, self.header_bottom + 1, self.chat_right, self.input_top)

    @property
    def search_point(self) -> tuple[int, int]:
        """The magnifier icon at the top of the session list."""
        return (self.list_right - 92, self.header_top + 35)

    @property
    def input_point(self) -> tuple[int, int]:
        return ((self.chat_left + self.chat_right) // 2, self.input_top + (self.height - self.input_top) // 3)


@dataclass(frozen=True)
class UnreadRow:
    badge_box: tuple[int, int, int, int]
    click_point: tuple[int, int]


class LayoutError(RuntimeError):
    pass


def _close(color: tuple[int, int, int], target: tuple[int, int, int], tol: int) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(color, target))


def _segments(row: list[tuple[int, int, int]], target: tuple[int, int, int], min_len: int) -> list[tuple[int, int]]:
    segs: list[tuple[int, int]] = []
    start = None
    for x, color in enumerate(row + [(-1, -1, -1)]):
        if _close(color, target, 2):
            if start is None:
                start = x
        elif start is not None:
            if x - start >= min_len:
                segs.append((start, x))
            start = None
    return segs


def detect_layout(image: Image.Image) -> Layout:
    img = image.convert("RGB")
    px = img.load()
    width, height = img.size

    # Rows inside the session list are cut by avatars, names and timestamps, so measure on
    # the first clean row just below the title bar, above the search box / icons.
    list_seg = chat_seg = None
    for y in range(36, min(160, height)):
        row = [px[x, y] for x in range(width)]
        lists = _segments(row, SESSION_LIST_BG, 150)
        if not lists:
            continue
        chats = [s for s in _segments(row, CHAT_BG, 300) if abs(s[0] - lists[0][1]) <= 3]
        if chats:
            list_seg, chat_seg = lists[0], chats[0]
            break
    if not list_seg or not chat_seg:
        raise LayoutError("无法识别微信主窗口布局：请确认窗口未最小化、处于聊天页且为浅色主题")

    # The left edge of the chat pane has a soft shadow, so probe a bit further in.
    probe_x = chat_seg[0] + 60

    def is_bg(y: int) -> bool:
        return _close(px[probe_x, y], CHAT_BG, 1)

    def is_divider(y: int) -> bool:
        # A 1-2px uniform gray line spanning the whole chat pane.
        samples = [px[x, y] for x in range(probe_x, chat_seg[1] - 60, 7)]
        return bool(samples) and all(215 <= c[0] <= 242 and c[0] == c[1] == c[2] for c in samples)

    header_top = 0
    while header_top < height // 4 and not is_bg(header_top):
        header_top += 1
    # The title text can sit on any probe column, so look for the divider line itself.
    header_bottom = next((y for y in range(header_top + 20, height // 3) if is_divider(y)), None)
    if header_bottom is None:
        # No chat is open (empty pane). The session list is still usable for searching.
        return Layout(
            width=width,
            height=height,
            list_left=list_seg[0],
            list_right=list_seg[1],
            chat_left=chat_seg[0],
            chat_right=chat_seg[1],
            header_top=header_top,
            header_bottom=header_top + 71,
            input_top=height - 300,
            chat_open=False,
        )

    input_top = None
    for y in range(height - 40, height // 2, -1):
        if is_divider(y) and is_bg(y - 2):
            input_top = y - 1 if is_divider(y - 1) else y
            break
    if input_top is None:
        raise LayoutError("无法识别输入框位置")

    # The middle row may be interrupted by message bubbles; the rows just above the
    # header divider and the input divider are always plain background.
    chat_right = chat_seg[1]
    for y in (header_bottom - 3, input_top - 3):
        segs = [seg for seg in _segments([px[x, y] for x in range(width)], CHAT_BG, 200) if seg[0] <= probe_x]
        if segs:
            chat_right = max(chat_right, segs[0][1])

    return Layout(
        width=width,
        height=height,
        list_left=list_seg[0],
        list_right=list_seg[1],
        chat_left=chat_seg[0],
        chat_right=chat_right,
        header_top=header_top,
        header_bottom=header_bottom,
        input_top=input_top,
    )


def find_unread_rows(image: Image.Image, layout: Layout) -> list[UnreadRow]:
    """Find red unread badges on session avatars (top-right corner of each avatar)."""
    img = image.convert("RGB")
    px = img.load()
    x0 = layout.list_left + 45
    x1 = min(layout.list_left + 100, layout.list_right)
    y0 = layout.header_bottom
    y1 = layout.height - 10

    mask: set[tuple[int, int]] = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if _close(px[x, y], BADGE_RED, 22):
                mask.add((x, y))

    rows: list[UnreadRow] = []
    seen: set[tuple[int, int]] = set()
    for start in sorted(mask, key=lambda p: (p[1], p[0])):
        if start in seen:
            continue
        stack = [start]
        comp: list[tuple[int, int]] = []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in mask and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        xs = [p[0] for p in comp]
        ys = [p[1] for p in comp]
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        if not (8 <= h <= 32 and 8 <= w <= 60 and len(comp) >= 0.45 * w * h):
            continue
        box = (min(xs), min(ys), max(xs), max(ys))
        click = ((layout.list_left + layout.list_right) // 2, box[1] + 30)
        rows.append(UnreadRow(badge_box=box, click_point=click))
    return rows
