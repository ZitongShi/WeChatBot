# WeChatCustomerBot

Windows desktop WeChat vision-assisted daily chat proxy prototype.

This first version is intentionally read-only by default:

- It detects WeChat windows.
- It screenshots a configured chat region.
- It uses a vision model to extract recent chat events.
- It deduplicates messages.
- It can generate a suggested daily-chat reply.
- It can manually send text when you explicitly run the `send` command.
- It does not auto-send replies unless `--auto-send` is explicitly enabled.

## Quick Start

```powershell
cd E:\WeChatCustomerBot
copy config.example.json config.json
$env:BOT_LLM_API_KEY="your-api-key"
$env:PYTHONPATH="E:\WeChatCustomerBot\src"
python -m wechat_customer_bot list-windows
python -m wechat_customer_bot watch --hwnd 132854 --suggest
```

Use an independent WeChat private chat window where possible. Do not use group chats for automation.

To allow automatic sending for a specific independent private chat window:

```powershell
python -m wechat_customer_bot watch --hwnd 132854 --auto-send
```

Only `suggest` decisions are sent. `ignore` and `handoff` decisions are never auto-sent.

For multiple windows, use one serial watcher instead of starting multiple `watch` processes:

```powershell
python -m wechat_customer_bot watch-many --hwnd 4329172 --hwnd 2362606 --auto-send
```

This avoids concurrent window activation and reduces the chance of reading the wrong window.

## Relay: forward a secondary account to the main account

`wechat_relay` watches the WeChat 4.x **main window** of the account logged in on this PC,
forwards every new private/group message to one chat (your main account), and sends your
replies back to the right sender.

How it works:

- Unread red badges in the session list are detected locally from pixels (no model call).
- The bot opens the chat and reads the title bar and the messages in one vision-model call
  (`qwen3.6:35b` on soclaas, ~1-4s). Left/right is then decided from pixels (green bubble,
  avatar side), not by the model.
- To open a chat by name, the vision model lists the session-list rows (or the search
  results) and the click is snapped to the avatar found locally at that position; the
  chat's title is then read back by the vision model and must match exactly before
  anything is typed. Windows OCR is only used for right-click menu items.
- New messages are found by anchoring on the last messages seen in that chat.
- It forwards them as one message per chat, then parks on `文件传输助手` so that every new
  message elsewhere shows up as a badge.
- Images are copied with WeChat's own `复制` menu and sent as originals. Voice bubbles are
  converted with WeChat's own `转文字` before reading.

Forward format:

```text
#12【群】专业班子
张三：今天几点开会？
李四：[图片] 会议室白板
```

Reply from the main account by **quoting** a forwarded message and typing, or with
`#12 内容`. `/list` shows recent chats, `/help` shows usage.

Setup:

```powershell
cd E:\zt-toys\WeChatBot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt winocr
copy relay.example.json relay.json        # edit forward_to if needed
# put SOCLAAS_API_KEY / SOCLAAS_BASE_URL into data\relay\soclaas.env
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m wechat_relay layout   # check detection
.\start_relay.ps1
```

Requirements and limits:

- WeChat light theme, main window not minimized; the bot drives mouse and keyboard, so do
  not use this PC while it runs.
- If the same account reads messages on a phone first, the PC badge disappears and the
  message is not forwarded.
- Only messages visible on screen are read; very long bursts are marked as possibly incomplete.

## Skill

The reply advisor loads:

```text
E:\WeChatCustomerBot\skills\wechat-tt-customer-service\SKILL.md
```


The raw exports stay local under `data\raw_wechat` and are ignored by git. The distilled notes are under `data\distilled`.

## Voice Messages

Voice bubbles are detected as `voice`. The current safe behavior is:

- if WeChat already shows a text transcript, treat the transcript as text
- if there is no visible transcript, do not guess the content
- return `handoff` and let the administrator or a later UI workflow perform WeChat's built-in "转文字"

Automatic right-click "转文字" is not enabled yet because it needs per-window menu calibration.

## Safety Boundary

The bot should not automatically handle:

- transfers, payments, collections, or loan requests
- verification codes, passwords, accounts, or private information
- meetups, addresses, schedules, or promises with real-world consequences
- serious emotional conflict, threats, complaints, or legal/illegal topics
- voice messages without visible transcript
- unclear screenshots or images

High-risk events should be handed to the human user.
