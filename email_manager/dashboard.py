"""Small localhost-only review dashboard for the safe email-manager pilot."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .models import ProcessedMessage, ReplyPreference, RunHistory
from .store import FEEDBACK_TYPES, Store


FEEDBACK_LABELS = {
    "draft_sent": "Draft was sent",
    "draft_edited": "Draft was edited",
    "draft_deleted": "Draft was deleted",
    "manual_draft_requested": "Needed a draft",
    "never_draft_like_this": "Don't draft like this",
}


def _selected(value: str, expected: str) -> str:
    return " selected" if value == expected else ""


def _feedback_form(message: ProcessedMessage) -> str:
    options = ['<option value="">Choose an outcome…</option>']
    for kind, label in FEEDBACK_LABELS.items():
        options.append(f'<option value="{kind}"{_selected(message.feedback_type or "", kind)}>{escape(label)}</option>')
    message_id = escape(message.message_id, quote=True)
    note = escape(message.feedback_note, quote=True)
    remove = (f'<button class="button subtle" name="action" value="remove" type="submit">Remove feedback</button>'
              if message.feedback_type else "")
    return f'''<form class="feedback-form" method="post" action="/feedback">
      <input type="hidden" name="message_id" value="{message_id}">
      <select name="feedback_type" aria-label="Reply outcome">{''.join(options)}</select>
      <input name="note" maxlength="1000" value="{note}" placeholder="Optional note">
      <button class="button" type="submit">Save feedback</button>{remove}
    </form>'''


def _decision(message: ProcessedMessage) -> tuple[str, str]:
    if message.has_draft:
        return "Reply draft created", "good"
    return message.draft_reason or "No reply draft was created.", "neutral"


def render_dashboard(messages: list[ProcessedMessage], preferences: list[ReplyPreference], runs: list[RunHistory] | None = None,
                     filters: dict[str, str] | None = None, notice: str = "") -> str:
    filters = filters or {}
    runs = runs or []
    cards = []
    for message in messages:
        decision, tone = _decision(message)
        source_link = (f'<a class="text-link" href="{escape(message.source_web_link, quote=True)}" target="_blank" rel="noreferrer">Open email ↗</a>'
                       if message.source_web_link else '<span class="muted">Source link unavailable</span>')
        draft_link = (f'<a class="text-link" href="{escape(message.draft_web_link, quote=True)}" target="_blank" rel="noreferrer">Open draft ↗</a>'
                      if message.draft_web_link else ('<span class="muted">Open Outlook Drafts to review</span>' if message.has_draft else ""))
        folder = f'<span class="detail">Suggested folder: <strong>{escape(message.suggested_folder)}</strong></span>' if message.suggested_folder else ""
        cards.append(f'''<article class="card">
          <div class="card-meta"><span class="tag {escape(message.category)}">{escape(message.category)}</span><time>{escape(message.processed_at.replace("T", " ")[:16])}</time></div>
          <h2>{escape(message.subject or "(no subject)")}</h2>
          <p class="sender">{escape(message.sender_email or "Legacy record — sender unavailable")}</p>
          <p class="summary">{escape(message.summary or "No summary was recorded.")}</p>
          <div class="decision {tone}"><strong>{escape(decision)}</strong></div>
          <div class="details">{folder}{source_link}{draft_link}</div>
          <div class="feedback-block"><label>Reply feedback</label>{_feedback_form(message)}</div>
        </article>''')
    preference_rows = "".join(
        f"<tr><td>{escape(item.sender_email)}</td><td><span class=\"tag {escape(item.category)}\">{escape(item.category)}</span></td>"
        f"<td>{item.positive_examples}</td><td>{item.negative_weight}</td><td>{item.confidence:.0%}</td>"
        f"<td>{'Drafts suppressed' if item.suppress_drafts else 'No suppression'}</td></tr>"
        for item in preferences
    ) or '<tr><td colspan="6" class="empty">No explicit feedback yet.</td></tr>'
    run_rows = "".join(
        f"<tr><td>{escape(run.run_at.replace('T', ' ')[:16])}</td><td>{run.processed}</td><td>{run.drafts_created}</td>"
        f"<td>{run.skipped}</td><td>{run.errors}</td></tr>" for run in runs
    ) or '<tr><td colspan="5" class="empty">Run history will appear after the next processing run.</td></tr>'
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    status = filters.get("status", "")
    category = filters.get("category", "")
    search = escape(filters.get("search", ""), quote=True)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Email Manager Review</title><style>
    :root{{--ink:#172033;--muted:#68758a;--line:#dde3ed;--navy:#142b4a;--blue:#2563eb;--page:#f5f7fb;--card:#fff;--green:#138a5a;--amber:#b45309;--red:#c2410c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}.shell{{max-width:1160px;margin:auto;padding:28px 22px 64px}}.hero{{background:linear-gradient(135deg,#112a4b,#1c4e82);color:#fff;border-radius:18px;padding:30px 32px;margin-bottom:22px;box-shadow:0 16px 30px #142b4a22}}.eyebrow{{color:#bbd8ff;font-size:.8rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase}}h1{{font-size:2rem;line-height:1.15;margin:7px 0}}.hero p{{margin:0;color:#d8e8fc;max-width:700px}}.safety{{display:inline-block;margin-top:16px;padding:6px 10px;border:1px solid #8cb6e8;border-radius:999px;font-size:.82rem}}.notice{{margin:18px 0;padding:12px 15px;background:#e3f8ed;border:1px solid #98d9b8;border-radius:9px;color:#075e39}}.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0;box-shadow:0 2px 8px #17203308}}.section-head{{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:14px}}h2,h3{{margin:0}}h2{{font-size:1.1rem}}h3{{font-size:1rem}}.muted,.empty{{color:var(--muted)}}.filters{{display:grid;grid-template-columns:1.4fr repeat(2,minmax(130px,.6fr)) auto;gap:10px;align-items:end}}label{{display:block;font-size:.78rem;font-weight:700;color:#45546a;margin-bottom:4px}}input,select{{width:100%;padding:9px 10px;border:1px solid #c9d3e0;border-radius:7px;background:#fff;color:var(--ink);font:inherit}}.button{{border:0;border-radius:7px;background:var(--blue);color:#fff;padding:9px 12px;font:inherit;font-weight:650;cursor:pointer;white-space:nowrap}}.button:hover{{background:#1d4ed8}}.button.subtle{{background:#eef2f7;color:#344154;border:1px solid #cbd5e1}}.queue{{display:grid;gap:13px}}.card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 20px;box-shadow:0 2px 7px #17203308}}.card-meta{{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:.82rem}}.card h2{{font-size:1.12rem;margin:9px 0 2px}}.sender{{margin:0;color:#4d5b70;font-weight:600}}.summary{{margin:12px 0}}.tag{{display:inline-block;border-radius:999px;padding:2px 8px;font-size:.75rem;font-weight:750;text-transform:capitalize;background:#e6edf7;color:#39516e}}.tag.action{{background:#dbeafe;color:#1d4ed8}}.tag.informational{{background:#e5f5eb;color:#087443}}.tag.marketing{{background:#fff1d7;color:#a85b00}}.tag.spam{{background:#fee4e2;color:#b42318}}.decision{{border-left:3px solid #94a3b8;background:#f7f9fc;padding:8px 10px;font-size:.9rem}}.decision.good{{border-color:var(--green);background:#ecfdf5;color:#066344}}.details{{display:flex;gap:14px;flex-wrap:wrap;margin:13px 0;font-size:.9rem}}.detail{{color:#56657a}}.text-link{{color:#155ec9;font-weight:650;text-decoration:none}}.text-link:hover{{text-decoration:underline}}.feedback-block{{border-top:1px solid #edf0f5;padding-top:13px}}.feedback-form{{display:grid;grid-template-columns:180px minmax(180px,1fr) auto auto;gap:8px;align-items:center}}.feedback-form label{{grid-column:1/-1}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}th,td{{padding:9px 8px;text-align:left;border-bottom:1px solid #edf0f5}}th{{color:#536176;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em}}@media(max-width:760px){{.shell{{padding:16px}}.hero{{padding:24px}}.filters,.feedback-form{{grid-template-columns:1fr}}.feedback-form .button{{width:100%}}.panel{{padding:16px}}.card{{padding:16px}}}}
    </style></head><body><main class="shell"><header class="hero"><div class="eyebrow">Local Outlook assistant</div><h1>Email Manager Review</h1><p>Review recent decisions, open Outlook to inspect messages and drafts, and teach the assistant your preferences.</p><span class="safety">Drafts only · This app cannot send, move, or delete email</span></header>{notice_html}
    <section class="panel"><div class="section-head"><div><h2>Review queue</h2><p class="muted">{len(messages)} matching decision(s)</p></div></div><form class="filters" method="get"><div><label for="search">Search sender or subject</label><input id="search" name="search" value="{search}" placeholder="e.g. proposal or client@example.com"></div><div><label for="category">Category</label><select id="category" name="category"><option value="">All categories</option>{''.join(f'<option value="{item}"{_selected(category,item)}>{item.title()}</option>' for item in ('action','informational','marketing','spam'))}</select></div><div><label for="status">Status</label><select id="status" name="status"><option value="">All decisions</option><option value="drafted"{_selected(status,'drafted')}>Draft created</option><option value="needs-review"{_selected(status,'needs-review')}>Reply needed, no draft</option><option value="action"{_selected(status,'action')}>Action needed</option></select></div><button class="button" type="submit">Filter</button></form></section>
    <section class="queue">{''.join(cards) or '<div class="panel empty">No messages match these filters.</div>'}</section>
    <section class="panel"><div class="section-head"><div><h2>Recent runs</h2><p class="muted">Local processing activity</p></div></div><table><thead><tr><th>Run time</th><th>Processed</th><th>Drafts</th><th>Skipped</th><th>Errors</th></tr></thead><tbody>{run_rows}</tbody></table></section>
    <section class="panel"><div class="section-head"><div><h2>Reply preferences</h2><p class="muted">Explicit feedback only. Suppression needs at least two negative points and 75% negative feedback.</p></div></div><table><thead><tr><th>Sender</th><th>Category</th><th>Positive</th><th>Negative weight</th><th>Negative share</th><th>Rule</th></tr></thead><tbody>{preference_rows}</tbody></table></section>
    </main></body></html>'''


def serve_dashboard(settings: Settings, port: int = 8765) -> None:
    """Serve only on loopback; each request uses a short-lived SQLite connection."""
    database_path = settings.database_path

    class DashboardHandler(BaseHTTPRequestHandler):
        def _render(self, filters: dict[str, str] | None = None, notice: str = "") -> None:
            filters = filters or {}
            store = Store(database_path)
            try:
                page = render_dashboard(
                    store.list_processed_messages(category=filters.get("category", ""), status=filters.get("status", ""), search=filters.get("search", "")),
                    store.list_reply_preferences(), store.list_runs(), filters, notice,
                )
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
                query = parse_qs(parsed.query)
                filters = {key: query.get(key, [""])[0] for key in ("category", "status", "search")}
                self._render(filters, "Feedback saved." if query.get("saved") else "")
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
            action = values.get("action", [""])[0]
            note = values.get("note", [""])[0]
            store = Store(database_path)
            try:
                if action == "remove":
                    store.remove_feedback(message_id)
                    notice = "Feedback removed."
                elif feedback_type in FEEDBACK_TYPES:
                    store.record_feedback(message_id, feedback_type, note)
                    notice = "Feedback saved."
                else:
                    self._render(notice="Feedback was not saved: choose an outcome first.")
                    return
            except ValueError as error:
                self._render(notice=f"Feedback was not saved: {error}")
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
