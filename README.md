# AI Email Manager

A safe pilot for Outlook triage: it reviews recent inbox messages on a schedule, categorizes them, creates proposed reply drafts, and creates an action-digest draft. It **never sends, moves, or deletes email**.

The original [notebook](email-tests.ipynb) remains an experiment. The `email_manager` package is the runnable service.

## Before using real company mail

Get written approval from the company’s Microsoft 365/IT and security owners for the Microsoft Graph application, the selected AI provider/account, data retention, and which mailbox may be used. Use a synthetic or approved test mailbox first.

## Setup

1. In Microsoft Entra ID, register a native/public client application and add delegated `User.Read`, `Mail.Read`, `Mail.ReadWrite`, and `MailboxSettings.ReadWrite` Microsoft Graph permissions. The first run uses Microsoft’s device-login flow.
2. Copy `.env.example` to `.env`, then set `CLIENT_ID`, `USER_EMAIL`, and `OPENAI_API_KEY`. Keep this file private.
3. Install the project with `uv sync`.
4. Run one safe processing pass:

   ```bash
   uv run email-manager run
   ```

5. After verifying the results, start the scheduler:

   ```bash
   uv run email-manager serve
   ```

`SCHEDULES` uses the local timezone of the machine running the process and defaults to `06:00,12:00,15:00`. The scheduler must run on an always-on, approved machine or service.

## Behavior

- Each message is recorded in `email-manager.db` after processing, preventing duplicate reply drafts during overlapping lookback windows.
- Outlook categories are added as `AI: Action needed`, `AI: Informational`, `AI: Marketing`, or `AI: Spam review`.
- A reply is drafted only when the model marks a message as requiring a response. You review and send it in Outlook.
- Reply drafts receive the configured `DRAFT_GREETING`, `DRAFT_CLOSING`, and `DRAFT_SIGNATURE`; the model is instructed to generate only the message body.
- Only action-needed messages are flagged. Automated/no-reply senders and configured `NO_REPLY_SENDERS` or `NO_REPLY_DOMAINS` never receive reply drafts.
- If action items exist, a digest is created as an Outlook draft rather than sent.
- `bootstrap-profiles --days 30` creates a small, bounded recipient profile from recent Sent-message metadata. It does not index full mailbox history or automatically move old email.
- `learn-folders` scans visible non-system folders and learns high-confidence sender-to-folder tendencies. Future messages from those senders receive an `AI: Suggested — <folder>` category; they are never moved.

## Commands

```bash
uv run email-manager run
uv run email-manager serve
uv run email-manager bootstrap-profiles --days 30
uv run email-manager learn-folders
```

## Future work

Near-real-time Microsoft Graph notifications, a review dashboard, AI-generated detailed contact profiles, reviewed folder moves, and sender/topic-triggered business automations are intentionally deferred until the pilot is approved and measured.


Codex Chat
019fedbd-38a0-76d1-b276-e2c60a43be75
