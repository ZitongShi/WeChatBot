# WeChatCustomerBot

Windows desktop WeChat vision-assisted customer service prototype.

This first version is intentionally read-only by default:

- It detects WeChat windows.
- It screenshots a configured chat region.
- It uses a vision model to extract recent chat events.
- It deduplicates messages.
- It can generate a suggested reply.
- It can manually send text when you explicitly run the `send` command.
- It does not auto-send customer replies.

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

## Skill

The reply advisor loads:

```text
E:\WeChatCustomerBot\skills\wechat-tt-customer-service\SKILL.md
```

The current skill inherits the QQ customer-service business rules and adds WeChat visual/OCR constraints plus style notes distilled from:

- 已读不回
- 煊
- 叶浪
- 鸿烨

The raw exports are under `data\raw_wechat`; the distilled notes are under `data\distilled`.

## Voice Messages

Voice bubbles are detected as `voice`. The current safe behavior is:

- if WeChat already shows a text transcript, treat the transcript as text
- if there is no visible transcript, do not guess the content
- return `handoff` and let the administrator or a later UI workflow perform WeChat's built-in "转文字"

Automatic right-click "转文字" is not enabled yet because it needs per-window menu calibration.

## Safety Boundary

The bot must not automatically:

- confirm payments
- create API keys
- recharge or modify quotas
- send final files
- handle refunds or compensation
- process remote-control codes

High-risk events should be handed to the administrator.
