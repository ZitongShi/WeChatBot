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

## Skill

The reply advisor loads:

```text
E:\WeChatCustomerBot\skills\wechat-tt-customer-service\SKILL.md
```

The current skill is for daily chat testing. It does not inherit the QQBot API-sales/customer-service workflow. It uses WeChat visual/OCR constraints plus style notes distilled from:

- 已读不回
- 煊
- 叶浪
- 鸿烨

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
