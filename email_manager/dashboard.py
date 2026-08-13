"""Small localhost-only review dashboard for the safe email-manager pilot."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .models import ProcessedMessage, ReplyPreference
from .store import FEEDBACK_TYPES, Store


FEEDBACK_LABELS = {
    "draft_sent": "Draft was sent",
    "draft_edited": "Draft was edited",
    "draft_deleted": "Draft was deleted",
    "manual_draft_requested": "Needed a draft",
    "never_draft_like_this": "Don't draft like this",
}


def _feedback_buttons(message_id: str) -> str:
    message = escape(message_id, quote=True)
    buttons = []
    for feedback_type, label in FEEDBACK_LABELS.items():
        buttons.append(
            f'<form method="post" action="/feedback"><input type="hidden" name="message_id" value="{message}">'
            f'<input type="hidden" name="feedback_type" value="{feedback_type}"><button>{escape(label)}</button></form>'
        )
    return '<div class="feedback">' + "".join(buttons) + "</div>"


def render_dashboard(messages: list[ProcessedMessage], preferences: list[ReplyPreference], notice: str = "") -> str:
    cards = []
    for message in messages:
        source_link = (f'<a href="{escape(message.source_web_link, quote=True)}" target="_blank" rel="noreferrer">Open source email</a>'
                       if message.source_web_link else '<span class="muted">Source link available after the next processing run</span>')
        status = "Reply draft created" if message.has_draft else "No reply draft"
        feedback = (f'<p class="feedback-status">Feedback: <strong>{escape(message.feedback_type)}</strong>'
                    f'{" — " + escape(message.feedback_note) if message.feedback_note else ""}</p>' if message.feedback_type else _feedback_buttons(message.message_id))
        folder = f'<p>Suggested folder: {escape(message.suggested_folder)}</p>' if message.suggested_folder else ""
        cards.append(f'''<article class="card">
          <div class="card-top"><span class="pill">{escape(message.category)}</span><span>{escape(status)}</span></div>
          <h2>{escape(message.subject or "(no subject)")}</h2>
          <p class="muted">From {escape(message.sender_email or "Unknown sender")} · {escape(message.processed_at)}</p>
          <p>{escape(message.summary or "No summary was recorded.")}</p>{folder}
          <p>{source_link}</p>{feedback}
        </article>''')
    preference_rows = "".join(
        f"<tr><td>{escape(item.sender_email)}</td><td>{escape(item.category)}</td><td>{item.positive_examples}</td>"
        f"<td>{item.negative_weight}</td><td>{item.confidence:.0%}</td><td>{'Suppress drafts' if item.suppress_drafts else 'No suppression'}</td></tr>"
        for item in preferences
    ) or '<tr><td colspan="6" class="muted">No explicit feedback yet.</td></tr>'
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Email Manager Review</title><style>
    body{{max-width:1100px;margin:32px auto;padding:0 18px;background:#f6f7fb;color:#1b2430;font:16px system-ui,sans-serif}} h1{{margin-bottom:4px}} .muted{{color:#657286}} .notice{{background:#dff5e8;padding:12px;border-radius:8px}} .card{{background:white;border:1px solid #dde3ec;border-radius:12px;padding:18px;margin:14px 0;box-shadow:0 1px 2px #00000008}} .card-top{{display:flex;justify-content:space-between;color:#526072;font-size:.9em}} h2{{font-size:1.15em;margin:10px 0}} .pill{{background:#e8eef9;padding:3px 8px;border-radius:999px;text-transform:capitalize}} .feedback{{display:flex;gap:7px;flex-wrap:wrap}} form{{display:inline}} button{{background:#164e8c;color:white;border:0;border-radius:6px;padding:7px 9px;cursor:pointer}} button:hover{{background:#0f3c6d}} .feedback-status{{background:#f0f4fa;padding:8px;border-radius:6px}} table{{width:100%;border-collapse:collapse;background:white}} th,td{{text-align:left;padding:9px;border-bottom:1px solid #dde3ec}} @media(max-width:650px){{body{{margin:16px auto}}}}
    </style></head><body><h1>Email Manager Review</h1><p class="muted">Local-only review queue. Feedback is explicit; this dashboard cannot send, move, or delete email.</p>{notice_html}
    <h2>Recent decisions</h2>{''.join(cards) or '<p class="muted">No processed messages yet. Run the email manager first.</p>'}
    <h2>Reply preferences</h2><table><thead><tr><th>Sender</th><th>Category</th><th>Positive</th><th>Negative weight</th><th>Negative share</th><th>Rule</th></tr></thead><tbody>{preference_rows}</tbody></table>
    </body></html>'''


def serve_dashboard(settings: Settings, port: int = 8765) -> None:
    """Serve only on loopback; each request uses a short-lived SQLite connection."""
    database_path = settings.database_path

    class DashboardHandler(BaseHTTPRequestHandler):
        def _render(self, notice: str = "") -> None:
            store = Store(database_path)
            try:
                page = render_dashboard(store.list_processed_messages(), store.list_reply_preferences(), notice)
            finally:
                store.close()
            encoded = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._render("Feedback saved." if parse_qs(parsed.query).get("saved") else "")
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/feedback":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            values = parse_qs(self.rfile.read(length).decode())
            message_id = values.get("message_id", [""])[0]
            feedback_type = values.get("feedback_type", [""])[0]
            if feedback_type not in FEEDBACK_TYPES:
                self._render("Feedback was not saved: invalid feedback type.")
                return
            store = Store(database_path)
            try:
                store.record_feedback(message_id, feedback_type)
            except ValueError as error:
                self._render(f"Feedback was not saved: {error}")
                return
            finally:
                store.close()
            self.send_response(303)
            self.send_header("Location", "/?saved=1")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Email Manager dashboard: http://127.0.0.1:{port} (localhost only; press Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
