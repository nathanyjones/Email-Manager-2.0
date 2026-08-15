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
- `learn-folder-profiles` sends a bounded sample from each non-system folder to the configured LLM and stores a compact local profile of its purpose, topics, and participant signals. With `FOLDER_SEMANTIC_SUGGESTIONS_ENABLED=true`, new messages are matched to these profiles using content, sender, To, and CC signals; only suggestions meeting the confidence threshold become categories.

## Commands

```bash
uv run email-manager run
uv run email-manager serve
uv run email-manager bootstrap-profiles --days 30
uv run email-manager learn-folders
uv run email-manager learn-folder-profiles
uv run email-manager feedback <source-message-id> draft_sent
uv run email-manager feedback-list
uv run email-manager feedback-summary
uv run email-manager dashboard
```

After profiling, inspect the compact local profiles before enabling semantic suggestions:

```bash
sqlite3 email-manager.db "SELECT folder_name, purpose, topics, participant_signals, examples_seen FROM folder_profiles ORDER BY folder_name;"
```

## Reply feedback

Record reply decisions manually after reviewing a draft in Outlook. The feedback stays in the local SQLite database; the app does not monitor, send, or inspect Outlook activity automatically.

```bash
uv run email-manager feedback <source-message-id> draft_sent
uv run email-manager feedback <source-message-id> draft_edited --note "Needed a shorter opening"
uv run email-manager feedback <source-message-id> draft_deleted
uv run email-manager feedback <source-message-id> manual_draft_requested
uv run email-manager feedback <source-message-id> never_draft_like_this --note "Receipt only"
uv run email-manager feedback-list
uv run email-manager feedback-summary
uv run email-manager feedback-remove <source-message-id>
```

The source message ID must already be present in local processing history. Feedback is one editable current decision per source message. The assessor receives an aggregate sender-level count as context, while automatic draft suppression is stricter: it applies only to the same sender and AI category after at least two negative points (one explicit `never_draft_like_this` counts as two) and at least 75% negative feedback. Hard no-reply rules always take precedence.

## Local review dashboard

Start the dashboard with `uv run email-manager dashboard`, then open <http://127.0.0.1:8765>. It is deliberately bound to localhost only. The Dashboard is a focused review queue: it shows only reply decisions awaiting feedback and asks one simple question at a time. Complete processing history is in Activity; Preferences is an editable local work-style profile for tone, reply length, greeting, closing, signature, and conservative versus balanced draft proactivity. It also exposes every underlying feedback decision so its derived sender/category effect can be removed. Hard no-reply, marketing/spam, and no-send safeguards always take precedence. The dashboard makes no Graph or OpenAI request by itself and cannot send, move, or delete messages.

For records processed before dashboard metadata was stored, run the following one-time, read-only refresh. It reads only the affected messages from Graph and backfills sender, subject, and Outlook links in the local database; it never changes Outlook mail.

```bash
uv run email-manager refresh-dashboard-metadata
```

## Private Outlook add-in proof of concept

The `outlook_addin/` folder is a private, sideloadable Outlook task-pane add-in. It is a thin Outlook interface over this same local service and database: it does not introduce another mailbox worker, token store, or draft policy. It shows a locally processed decision for the currently selected message, records the same explicit feedback as the dashboard, and can answer an explicit question about that one message. It never sends, moves, deletes, or automatically scans mail.

For the private pilot, serve it only from your laptop over trusted localhost HTTPS. First install [mkcert](https://github.com/FiloSottile/mkcert), then create a development certificate (keep the key private):

```bash
mkcert -install
mkcert -cert-file localhost.pem -key-file localhost-key.pem localhost 127.0.0.1 ::1
uv run email-manager outlook-addin --certificate localhost.pem --private-key localhost-key.pem
```

In Outlook on the web, open **Get Add-ins** → **My add-ins** → **Add a custom add-in** → **Add from file**, then select [`outlook_addin/manifest.xml`](outlook_addin/manifest.xml). Open any processed email and choose **Email Manager** from the message action bar. The task pane and its local API are available only at `https://localhost:8766`; do not expose this development server to a network.

The task pane can be pinned in supported Outlook clients. While pinned it quietly refreshes the local decision as the selected message changes; selection alone never reads the message from Graph or calls the AI provider. A message without a local decision shows **Analyze this email**. That explicit click uses the exact same idempotent processing path, categories, flags, feedback rules, and draft safeguards as the scheduler. The panel’s decision card includes priority, summary, proposed next steps, suggested timing, rationale, draft state, and explicit draft opening; questions (including quick questions) call the AI provider only after a click.

For a Microsoft 365 work rollout, replace the localhost URLs with an approved HTTPS domain, move the local bridge/database to approved hosting, use Entra sign-in, and let IT deploy the manifest to a small pilot group through Microsoft 365 Integrated Apps. Do not sideload this private manifest into a work mailbox without approval.

## Future work

Near-real-time Microsoft Graph notifications, a review dashboard, AI-generated detailed contact profiles, reviewed folder moves, and sender/topic-triggered business automations are intentionally deferred until the pilot is approved and measured.


Codex Chat
019fedbd-38a0-76d1-b276-e2c60a43be75
