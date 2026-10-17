from PIL import Image, ImageDraw

from wechat_relay.layout import BADGE_RED, CHAT_BG, SESSION_LIST_BG, detect_layout, find_unread_rows
from wechat_relay.relay import format_forward, parse_command
from wechat_relay.store import RelayState, split_after_anchor
from wechat_relay.ui import is_group_title, names_match, strip_member_count
from wechat_relay.vision import ChatMessage, parse_chat_read, parse_json_object


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
    assert is_group_title("专业班子(32)") and not is_group_title("zitong")
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


def test_pick_search_result_prefers_contact_title():
    from wechat_relay.screen import OcrLine
    from wechat_relay.ui import pick_search_result

    rows = [
        (29, 54, "联系人"), (99, 120, "zitong"), (226, 120, "专业班子"), (262, 120, "包含:zitong"),
        (413, 54, "聊天记录"), (503, 122, "[文件]260927zitong.md"), (948, 203, "zitong"),
    ]
    lines = [OcrLine(text=t, x=x, y=y, width=60, height=20) for y, x, t in rows]
    assert pick_search_result(lines, "zitong").y == 99
    assert pick_search_result(lines, "专业班子").y == 226
    assert pick_search_result(lines, "马明宇") is None
    feature = [(31, 88, "搜索网络结果"), (79, 53, "0 文件传輸助手"), (337, 57, "功能"), (403, 124, "文件传输助手"), (474, 113, "[聊天记录]")]
    feature_lines = [OcrLine(text=t, x=x, y=y, width=60, height=20) for y, x, t in feature]
    assert pick_search_result(feature_lines, "文件传输助手").y == 403
    garbled = [OcrLine(text="文亻牛传输助手", x=120, y=99, width=60, height=20)]
    assert pick_search_result(garbled, "文件传输助手") is not None
