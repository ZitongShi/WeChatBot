from PIL import Image, ImageDraw

from wechat_relay.layout import (
    BADGE_RED,
    CHAT_BG,
    SELECTED_ROW,
    SESSION_LIST_BG,
    detect_layout,
    find_list_rows,
    find_unread_rows,
    snap_to_row,
)
from wechat_relay.relay import format_forward, parse_command
from wechat_relay.store import RelayState, split_after_anchor
from wechat_relay.ui import names_match, pick_row, same_name, strip_member_count
from wechat_relay.vision import (
    MY_BUBBLE,
    ChatMessage,
    ChatRead,
    Row,
    fix_sides,
    parse_chat_read,
    parse_json_object,
    parse_rows,
)


def msg(text, side="L", kind="text", sender="", quote=""):
    return ChatMessage(side=side, sender=sender, type=kind, text=text, quote=quote)


def test_anchor_returns_only_messages_after_last_seen():
    old = [msg("a"), msg("b"), msg("me", side="R")]
    now = old + [msg("c"), msg("d")]
    new, found = split_after_anchor(now, [m.signature for m in now], [m.signature for m in old])
    assert found and [m.text for m in new] == ["c", "d"]


def test_anchor_survives_scrolling():
    old = [msg("a"), msg("b"), msg("c")]
    now = [msg("b"), msg("c"), msg("d")]
    new, found = split_after_anchor(now, [m.signature for m in now], [m.signature for m in old])
    assert found and [m.text for m in new] == ["d"]


def test_repeated_text_is_still_new():
    old = [msg("好"), msg("ok", side="R")]
    now = old + [msg("好")]
    new, _ = split_after_anchor(now, [m.signature for m in now], [m.signature for m in old])
    assert [m.text for m in new] == ["好"]


def test_no_anchor_takes_messages_after_my_last_reply():
    now = [msg("old"), msg("mine", side="R"), msg("x"), msg("y")]
    new, found = split_after_anchor(now, [m.signature for m in now], [])
    assert not found and [m.text for m in new] == ["x", "y"]


def test_state_ids_and_targets(tmp_path):
    state = RelayState(path=tmp_path / "s.json")
    a = state.new_forward("专业班子", True)
    b = state.new_forward("李四", False)
    state.save()
    loaded = RelayState.load(tmp_path / "s.json")
    assert loaded.forward_target(a) == "专业班子"
    assert loaded.forward_target(b) == "李四"
    assert loaded.recent_chats()[0] == (b, "李四")


def test_parse_command_variants():
    assert parse_command(msg("#12 好的")).target_id == 12
    assert parse_command(msg("#12：好的")).text == "好的"
    quoted = parse_command(msg("收到", quote="#7【群】专业班子\n张三：开会吗"))
    assert (quoted.kind, quoted.target_id, quoted.text) == ("reply", 7, "收到")
    assert parse_command(msg("/list")).kind == "list"
    assert parse_command(msg("随便说说")).kind == "unknown"


def test_format_forward_group_and_private():
    text = format_forward(3, "专业班子", True, [msg("开会", sender="张三"), msg("", kind="image", sender="李四")], True)
    assert text.splitlines() == ["#3【群】专业班子", "张三：开会", "李四：[图片]"]
    private = format_forward(4, "李四", False, [msg("在吗")], False)
    assert private.splitlines()[0] == "#4【私】李四"
    assert private.splitlines()[-1].startswith("（可能")


def test_parse_chat_read_fixes_side_from_box():
    data = parse_json_object(
        '```json\n{"group":false,"messages":[{"side":"L","type":"image","text":"x","box":[500,10,890,200]},'
        '{"side":"R","type":"text","text":"hi","box":[100,300,400,350]}]}\n```'
    )
    read = parse_chat_read(data, (1000, 1000))
    assert [m.side for m in read.messages] == ["R", "L"]
    assert read.messages[0].box == (500, 10, 890, 200)


def test_title_helpers():
    assert strip_member_count("专业班子（32）") == "专业班子"
    assert names_match("zitong", "zitong") and names_match("文件传输助手", "文件传输助手")
    assert not names_match("zitong", "专业班子")


def synthetic_window():
    img = Image.new("RGB", (1000, 1200), (224, 224, 224))
    draw = ImageDraw.Draw(img)
    draw.rectangle((100, 40, 349, 1199), fill=SESSION_LIST_BG)
    draw.rectangle((350, 40, 999, 1199), fill=CHAT_BG)
    draw.line((350, 110, 999, 110), fill=(240, 240, 240))
    draw.line((350, 950, 999, 950), fill=(231, 231, 231))
    draw.ellipse((160, 300, 172, 312), fill=BADGE_RED)
    return img


def test_detect_layout_and_badges():
    img = synthetic_window()
    layout = detect_layout(img)
    assert (layout.list_left, layout.chat_left, layout.chat_right) == (100, 350, 1000)
    assert layout.header_bottom == 110 and layout.input_top == 950
    rows = find_unread_rows(img, layout)
    assert len(rows) == 1 and rows[0].badge_box[1] == 300


def test_list_rows_found_from_avatars_and_snapped():
    img = synthetic_window()
    draw = ImageDraw.Draw(img)
    draw.rectangle((100, 191, 349, 271), fill=SELECTED_ROW)  # the open chat's row
    for top in (127, 208, 289):
        draw.rectangle((110, top, 155, top + 45), fill=(90, 120, 200))
    draw.rectangle((110, 230, 155, 233), fill=SELECTED_ROW)  # avatar pixels matching the row color
    layout = detect_layout(img)
    centers = find_list_rows(img, layout)
    assert centers == [150, 231, 312]
    assert snap_to_row(205, centers) == 231 and snap_to_row(700, centers) is None
    assert snap_to_row(190, centers) is None  # about halfway between two rows
    assert snap_to_row(270, centers, index=2, first_y=155) == 312  # position wins over drifted boxes
    assert snap_to_row(270, centers, index=2, first_y=200) is None  # first rows disagree


def test_fix_sides_trusts_pixels_over_the_model():
    img = Image.new("RGB", (640, 400), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((150, 20, 550, 120), fill=MY_BUBBLE)  # my long green bubble, centered-ish
    draw.rectangle((25, 150, 70, 195), fill=(140, 90, 60))  # left avatar
    draw.rectangle((90, 150, 300, 190), fill=(255, 255, 255))  # their white file card
    draw.rectangle((570, 250, 615, 295), fill=(200, 190, 220))  # right avatar
    draw.rectangle((350, 250, 550, 290), fill=(255, 255, 255))  # my white file card
    read = ChatRead(
        is_group=False,
        messages=[
            ChatMessage("L", "", "text", "a", box=(140, 15, 560, 125)),
            ChatMessage("R", "", "file", "b", box=(90, 150, 300, 190)),
            ChatMessage("L", "", "file", "c", box=(350, 250, 550, 290)),
            ChatMessage("C", "", "system", "d", box=(250, 330, 390, 350)),
        ],
    )
    fix_sides(read, img)
    assert [m.side for m in read.messages] == ["R", "L", "R", "C"]


def test_parse_chat_read_title_and_group():
    data = {"title": "专业班子", "member_count": 32, "group": False, "messages": []}
    read = parse_chat_read(data, (100, 100))
    assert (read.title, read.member_count, read.is_group) == ("专业班子", 32, True)
    # The count left in the title still counts; nickname labels alone do not make a group.
    read = parse_chat_read({"title": "专业班子（5）", "member_count": None}, (100, 100))
    assert (read.title, read.member_count, read.is_group) == ("专业班子", 5, True)
    read = parse_chat_read({"title": "李雷", "member_count": None, "group": True}, (100, 100))
    assert (read.title, read.is_group) == ("李雷", False)


def test_same_name_is_strict_but_accepts_cut_off_names():
    assert same_name("李雷", "李雷") and same_name("zitong", " zitong ")
    assert not same_name("李雷", "李雷楠") and not same_name("zitong", "zltong")
    assert same_name("[UnderWay] CV group", "[UnderWay] CV...")
    assert same_name("[UnderWay] CV group", "[UnderWay] CV…")
    assert not same_name("[UnderWay] CV group", "[UnderWay] CV")
    assert not same_name("李雷", "李...")  # a 1-glyph prefix is too weak


def test_parse_rows_and_pick_row():
    data = parse_json_object(
        '{"rows":[{"section":"联系人","name":"李雷","box":[0,60,1000,140]},'
        '{"section":"群聊","name":"专业班子","box":[0,180,1000,260]},'
        '{"section":"搜索网络结果","name":"李雷","box":[0,500,1000,540]},'
        '{"section":"聊天记录","name":"zitong","box":[0,900,1000,960]},'
        '{"name":"","box":[0,0,1,1]},{"name":"bad","box":[0,0,1]}]}'
    )
    rows = parse_rows(data, (400, 600))
    assert [r.name for r in rows] == ["李雷", "专业班子", "李雷", "zitong"]
    assert rows[0].center == (200, 60)
    allowed = ("联系人", "群聊", "功能", "最常使用")
    assert pick_row(rows, "李雷", allowed).center == (200, 60)
    assert pick_row(rows, "zitong", allowed) is None  # chat-history hits open a separate window
    assert pick_row(rows, "zitong").section == "聊天记录"
    assert pick_row([Row("文件传输助手", (5, 5))], "文件传输助手") is not None


def test_split_out_quote_is_folded_into_the_reply():
    from wechat_relay.relay import attach_split_quotes

    messages = [
        msg("/help"),
        msg("好的收到"),
        msg("BQ7: #1【私】李雷 ok 爸爸"),
        msg("#2【群】专业班子 张三：开会", side="R"),
    ]
    merged = attach_split_quotes(messages)
    assert [m.text for m in merged] == ["/help", "好的收到", "#2【群】专业班子 张三：开会"]
    assert parse_command(merged[1]).target_id == 1 and parse_command(merged[1]).text == "好的收到"


def test_split_out_quote_is_recognised():
    from wechat_relay.relay import FORWARD_QUOTE

    assert FORWARD_QUOTE.search("BQ7: #1【私】李雷 ok （可能还有更早的消息未显示）")
    assert FORWARD_QUOTE.search("#12【群】专业班子 张三：开会")
    assert not FORWARD_QUOTE.search("#1 好的收到")
