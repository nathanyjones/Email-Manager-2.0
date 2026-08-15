"""Local-only, server-rendered control center for the email-manager pilot."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .models import FeedbackRecord, FolderProfile, ProcessedMessage, ReplyPreference, RunHistory, WorkStyleProfile
from .store import FEEDBACK_TYPES, Store


FEEDBACK_LABELS = {
    "draft_sent": "Draft was sent", "draft_edited": "Draft was edited", "draft_deleted": "Draft was deleted",
    "draft_not_needed_once": "Draft not needed once", "manual_draft_requested": "Needed a draft", "never_draft_like_this": "Don't draft like this", "no_draft_correct": "No draft was needed",
}
NAV_ITEMS = (("/", "Dashboard", "dashboard"), ("/activity", "Activity", "activity"),
             ("/preferences", "Preferences", "preferences"), ("/folders", "Folders", "folders"),
             ("/settings", "Settings", "settings"))

STYLE = """
:root{--ink:#17243a;--muted:#687890;--line:#dbe4f0;--blue:#2868d7;--page:#f4f7fc;--card:#fff;--green:#087a52;--shadow:0 14px 38px #19345a12}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 5% 0,#e9f2ff 0,transparent 31rem),var(--page);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}.shell{max-width:1180px;margin:auto;padding:22px 22px 70px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:15px}.brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink);font-weight:800;letter-spacing:-.02em}.brand-mark{display:grid;place-items:center;width:31px;height:31px;border-radius:9px;background:linear-gradient(135deg,#0d294b,#2879c3);color:#fff;box-shadow:0 5px 12px #1e579e36}.local{display:inline-flex;align-items:center;gap:7px;color:#486079;font-size:.8rem;font-weight:700}.local:before{content:"";width:7px;height:7px;border-radius:50%;background:#38c684;box-shadow:0 0 0 3px #38c68422}.app-nav{display:flex;gap:4px;overflow:auto;padding:5px;background:#eaf0f9;border:1px solid #d9e3f1;border-radius:12px}.app-nav a{padding:7px 11px;border-radius:8px;color:#53647b;font-size:.84rem;font-weight:750;text-decoration:none;white-space:nowrap}.app-nav a:hover{background:#fff;color:#1e5abe}.app-nav a.active{background:#fff;color:#1559bd;box-shadow:0 2px 6px #1c3c6815}.hero{position:relative;overflow:hidden;background:linear-gradient(122deg,#0c2545 0%,#164b7d 55%,#2777bd 130%);color:#fff;border-radius:22px;padding:33px 36px 28px;box-shadow:0 20px 46px #133a6926;animation:hero-in .55s ease-out both}.hero:before,.hero:after{content:"";position:absolute;border-radius:50%;pointer-events:none}.hero:before{width:330px;height:330px;right:-85px;top:-165px;background:#70b9ff29}.hero:after{width:180px;height:180px;right:150px;bottom:-130px;background:#88cdf91f}.hero>*{position:relative;z-index:1}.eyebrow{color:#b8ddff;font-size:.76rem;font-weight:750;letter-spacing:.11em;text-transform:uppercase}h1{font-size:clamp(2rem,4vw,2.55rem);line-height:1.1;letter-spacing:-.035em;margin:7px 0 10px}.hero p{margin:0;color:#d8eaff;max-width:690px;font-size:1.02rem}.hero-bottom{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-top:24px}.safety{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;background:#0d274980;border:1px solid #91bce677;border-radius:999px;font-size:.8rem}.safety:before{content:"";width:7px;height:7px;border-radius:50%;background:#6ee7b7}.hero-stats{display:flex;gap:22px}.stat{min-width:64px}.stat strong{display:block;font-size:1.45rem;line-height:1;font-variant-numeric:tabular-nums}.stat span{color:#bcd9f5;font-size:.75rem}.notice{margin:18px 0;padding:12px 15px;background:#e3f8ed;border:1px solid #98d9b8;border-radius:10px;color:#075e39;animation:rise .3s ease-out both}.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:21px;margin:18px 0;box-shadow:var(--shadow)}.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:14px}h2{font-size:1.13rem;letter-spacing:-.015em;margin:0}.section-head p,.muted{color:var(--muted);margin:3px 0 0}.filters{display:grid;grid-template-columns:1.4fr repeat(2,minmax(130px,.6fr)) auto;gap:10px;align-items:end}label{display:block;font-size:.77rem;font-weight:760;color:#4a5a70;margin-bottom:5px}input,select,textarea{width:100%;padding:10px 11px;border:1px solid #cbd7e6;border-radius:8px;background:#fff;color:var(--ink);font:inherit}textarea{min-height:82px;resize:vertical}input:focus,select:focus,textarea:focus{outline:0;border-color:#5792ed;box-shadow:0 0 0 3px #4f8ff51e}.button,.review-flow button{border:0;border-radius:8px;background:linear-gradient(135deg,#2d72df,#1e58bb);color:#fff;padding:10px 13px;font:inherit;font-weight:720;cursor:pointer;white-space:nowrap;box-shadow:0 3px 8px #235cad26;transition:transform .18s,box-shadow .18s}.button:hover,.review-flow button:hover{transform:translateY(-1px);box-shadow:0 6px 13px #235cad30}.button.subtle,.review-flow button[data-next],.review-flow button[data-cancel]{background:#f4f7fb;color:#405068;border:1px solid #cbd7e6;box-shadow:none}.queue{display:grid;gap:14px}.card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:20px 21px;box-shadow:0 4px 12px #172f5009;animation:rise .42s ease-out var(--delay) both;transition:transform .2s,box-shadow .2s,border-color .2s}.card:hover{transform:translateY(-2px);border-color:#b9cde7;box-shadow:0 13px 28px #17385a13}.card-meta{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:.81rem}.card h2{font-size:1.16rem;line-height:1.3;margin:11px 0 2px}.sender{margin:0;color:#4b5c73;font-weight:650}.summary{margin:13px 0;color:#28364b}.tag{display:inline-block;border-radius:999px;padding:3px 9px;font-size:.72rem;font-weight:780;text-transform:capitalize;background:#e6edf7;color:#39516e}.tag.action{background:#dbeafe;color:#1e58bb}.tag.informational{background:#e2f6eb;color:#08734b}.tag.marketing{background:#fff1d7;color:#a45a00}.tag.spam{background:#fee4e2;color:#b42318}.decision{border-left:3px solid #94a3b8;background:#f7f9fc;padding:9px 11px;border-radius:0 8px 8px 0;font-size:.9rem}.decision.good{border-color:var(--green);background:#ecfdf5;color:#066344}.details{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:15px 0;font-size:.9rem}.detail{color:#56657a;margin-right:auto}.action-link{display:inline-flex;gap:5px;color:#155ec9;font-weight:720;text-decoration:none;padding:5px 8px;border-radius:7px;background:#edf5ff}.draft-link{background:#e9f9f1;color:#08734b}.feedback-block{border-top:1px solid #edf1f6;padding-top:14px}.review-flow{display:grid;gap:9px}.flow-step{background:#f7faff;border:1px solid #e0eafa;border-radius:10px;padding:12px}.flow-step>div{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}.flow-note{display:grid;gap:8px;background:#f7faff;border:1px solid #e0eafa;border-radius:10px;padding:12px}.flow-note>div{display:flex;gap:8px}.flow-save{display:none}table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{padding:10px 8px;text-align:left;border-bottom:1px solid #edf0f5}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f8fbff}th{color:#64748b;font-size:.72rem;text-transform:uppercase;letter-spacing:.055em}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.setting,.folder-card{padding:16px;border:1px solid var(--line);border-radius:12px;background:linear-gradient(135deg,#fff,#f9fbff)}.setting small,.folder-card small{display:block;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em}.setting strong{display:block;margin-top:4px;font-size:1.02rem}.folder-card h3{margin:5px 0;font-size:1rem}.folder-card p{margin:6px 0;color:#47566b}.topic{display:inline-block;margin:4px 4px 0 0;padding:3px 7px;border-radius:6px;background:#eaf2ff;color:#295b9f;font-size:.78rem}.empty-state{text-align:center;padding:36px 18px;color:var(--muted)}.empty-state strong{display:block;color:#43526a;font-size:1rem;margin-bottom:4px}@keyframes hero-in{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}@keyframes rise{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.01ms!important;transition:none!important}}@media(max-width:760px){.shell{padding:16px}.topbar,.hero-bottom{align-items:start;flex-direction:column}.hero{padding:27px 24px 24px}.hero-stats{gap:20px}.app-nav{width:100%}.filters,.grid{grid-template-columns:1fr}.panel{padding:17px}.card{padding:17px}.detail{width:100%;margin-right:0}table{display:block;overflow-x:auto}}
"""


def _selected(value: str, expected: str) -> str:
    return " selected" if value == expected else ""


def _layout(active: str, title: str, subtitle: str, body: str, stats: tuple[tuple[int, str], ...] = (), notice: str = "") -> str:
    nav = "".join(f'<a class="{"active" if key == active else ""}" href="{path}">{label}</a>' for path, label, key in NAV_ITEMS)
    stat_html = "".join(f'<div class="stat"><strong>{value}</strong><span>{escape(label)}</span></div>' for value, label in stats)
    notice_html = f'<div class="notice">{escape(notice)}</div>' if notice else ""
    script = '''<script>document.addEventListener("click",function(event){const button=event.target.closest("[data-save],[data-note],[data-next],[data-cancel]");if(!button)return;const flow=button.closest(".review-flow");if(!flow)return;if(button.dataset.save){const form=flow.querySelector(".flow-save");form.querySelector("[name=feedback_type]").value=button.dataset.save;form.submit()}if(button.dataset.next){flow.querySelectorAll(".flow-step").forEach(function(step){step.hidden=step.dataset.step!==button.dataset.next})}if(button.dataset.note){const form=flow.querySelector(".flow-note");form.hidden=false;form.querySelector("[name=feedback_type]").value=button.dataset.note;form.querySelector("label").textContent=button.dataset.prompt;form.querySelector("textarea").focus()}if(button.hasAttribute("data-cancel")){flow.querySelector(".flow-note").hidden=true;flow.querySelectorAll(".flow-step").forEach(function(step){step.hidden=false})}});</script>'''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · Email Manager</title><style>{STYLE}</style></head><body><main class="shell"><div class="topbar"><a class="brand" href="/"><span class="brand-mark">✦</span>Email Manager</a><span class="local">Local-only pilot</span></div><nav class="app-nav" aria-label="Email Manager">{nav}</nav><header class="hero"><div class="eyebrow">Local Outlook assistant</div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p><div class="hero-bottom"><span class="safety">Drafts only · Nothing is sent, moved, or deleted</span><div class="hero-stats">{stat_html}</div></div></header>{notice_html}{body}</main>{script}</body></html>'''


def _review_flow(message: ProcessedMessage) -> str:
    message_id = escape(message.message_id, quote=True)
    if message.has_draft:
        prompt = "Was this draft useful?"
        choices = '<button type="button" data-save="draft_sent">Sent it</button><button type="button" data-note="draft_edited" data-prompt="What did you change?">Edited it</button><button type="button" data-next="unneeded">Didn’t need it</button>'
        followup = '<div class="flow-step" data-step="unneeded" hidden><strong>Should we avoid similar drafts?</strong><div><button type="button" data-save="draft_not_needed_once">Only this email</button><button type="button" data-note="never_draft_like_this" data-prompt="What should be different next time?">Avoid similar emails</button></div></div>'
    else:
        prompt = "Was it correct not to draft a reply?"
        choices = '<button type="button" data-save="no_draft_correct">Yes, correct</button><button type="button" data-note="manual_draft_requested" data-prompt="What did the assistant miss?">No, I needed one</button>'
        followup = ""
    return f'''<div class="review-flow"><div class="flow-step"><strong>{prompt}</strong><div>{choices}</div></div>{followup}<form class="flow-note" method="post" action="/feedback" hidden><input type="hidden" name="message_id" value="{message_id}"><input type="hidden" name="feedback_type"><label></label><textarea name="note" maxlength="1000"></textarea><div><button class="button">Save feedback</button><button class="button subtle" type="button" data-cancel>Cancel</button></div></form><form class="flow-save" method="post" action="/feedback"><input type="hidden" name="message_id" value="{message_id}"><input type="hidden" name="feedback_type"></form></div>'''


def _preference_rows(preferences: list[ReplyPreference]) -> str:
    return "".join(f'<tr><td>{escape(item.sender_email)}</td><td><span class="tag {escape(item.category)}">{escape(item.category)}</span></td><td>{item.positive_examples}</td><td>{item.negative_weight}</td><td>{item.confidence:.0%}</td><td>{"Drafts suppressed" if item.suppress_drafts else "No suppression"}</td></tr>' for item in preferences) or '<tr><td colspan="6" class="muted">No explicit feedback yet.</td></tr>'


def render_dashboard(messages: list[ProcessedMessage], preferences: list[ReplyPreference], runs: list[RunHistory] | None = None, filters: dict[str, str] | None = None, notice: str = "") -> str:
    del preferences, runs, filters
    cards = []
    for index, message in enumerate(messages):
        reason = "Reply draft created" if message.has_draft else (message.draft_reason or "No reply draft was created.")
        source = f'<a class="action-link" href="{escape(message.source_web_link, quote=True)}" target="_blank" rel="noreferrer">Open email ↗</a>' if message.source_web_link else '<span class="muted">Source link unavailable</span>'
        draft = f'<a class="action-link draft-link" href="{escape(message.draft_web_link, quote=True)}" target="_blank" rel="noreferrer">Open draft ↗</a>' if message.draft_web_link else ('<span class="muted">Open Outlook Drafts to review</span>' if message.has_draft else "")
        folder = f'<span class="detail">Suggested folder: <strong>{escape(message.suggested_folder)}</strong></span>' if message.suggested_folder else ""
        cards.append(f'<article class="card" style="--delay:{min(index, 12) * 35}ms"><div class="card-meta"><span class="tag {escape(message.category)}">{escape(message.category)}</span><time>{escape(message.processed_at.replace("T", " ")[:16])}</time></div><h2>{escape(message.subject or "(no subject)")}</h2><p class="sender">{escape(message.sender_email or "Legacy record — sender unavailable")}</p><p class="summary">{escape(message.summary or "No summary was recorded.")}</p><div class="decision {"good" if message.has_draft else ""}"><strong>{escape(reason)}</strong></div><div class="details">{folder}{source}{draft}</div><div class="feedback-block">{_review_flow(message)}</div></article>')
    body = '<section class="panel"><div class="section-head"><div><h2>Needs your review</h2><p>Only reply decisions awaiting feedback appear here. Full history is in Activity.</p></div><a class="action-link" href="/activity">Open activity ↗</a></div></section>' + f'<section class="queue">{"".join(cards) or "<div class=\"panel empty-state\"><strong>You’re all caught up.</strong>No reply decisions need your feedback right now.</div>"}</section>'
    stats = ((len(messages), "to review"), (sum(item.has_draft for item in messages), "drafts"), (sum(not item.has_draft for item in messages), "missed drafts"))
    return _layout("dashboard", "Review queue", "Make one quick decision at a time. The assistant learns only from the feedback you choose to give.", body, stats, notice)


def render_activity(runs: list[RunHistory], messages: list[ProcessedMessage] | None = None) -> str:
    messages = messages or []
    rows = "".join(f'<tr><td>{escape(item.run_at.replace("T", " ")[:16])}</td><td>{item.processed}</td><td>{item.drafts_created}</td><td>{item.skipped}</td><td>{item.errors}</td></tr>' for item in runs) or '<tr><td colspan="5" class="muted">Run history will appear after the next processing run.</td></tr>'
    decisions = "".join(f'<tr><td>{escape(item.processed_at.replace("T", " ")[:16])}</td><td>{escape(item.sender_email or "Legacy record")}</td><td>{escape(item.subject or "(no subject)")}</td><td><span class="tag {escape(item.category)}">{escape(item.category)}</span></td><td>{"Draft" if item.has_draft else "No draft"}</td></tr>' for item in messages) or '<tr><td colspan="5" class="muted">No local decisions recorded yet.</td></tr>'
    body = f'<section class="panel"><div class="section-head"><div><h2>Processing history</h2><p>Every completed local inbox pass is recorded here.</p></div></div><table><thead><tr><th>Run time</th><th>Processed</th><th>Drafts</th><th>Skipped</th><th>Errors</th></tr></thead><tbody>{rows}</tbody></table></section><section class="panel"><div class="section-head"><div><h2>All recent decisions</h2><p>This is the complete local history, including items not shown in the review queue.</p></div></div><table><thead><tr><th>Processed</th><th>Sender</th><th>Subject</th><th>Category</th><th>Reply</th></tr></thead><tbody>{decisions}</tbody></table></section>'
    return _layout("activity", "Activity", "See the local processing history and spot runs that need attention.", body, ((len(runs), "recent runs"), (sum(item.processed for item in runs), "processed"), (sum(item.drafts_created for item in runs), "drafts")))


def render_preferences(preferences: list[ReplyPreference], profile: WorkStyleProfile | None = None, feedback: list[FeedbackRecord] | None = None) -> str:
    profile, feedback = profile or WorkStyleProfile(), feedback or []
    def val(value: str) -> str: return escape(value, quote=True)
    feedback_rows = "".join(f'<tr><td>{escape(item.recorded_at[:16].replace("T", " "))}</td><td>{escape(item.sender_email)}</td><td>{escape(item.category)}</td><td>{escape(FEEDBACK_LABELS.get(item.feedback_type, item.feedback_type))}</td><td>{escape(item.note)}</td><td><form method="post" action="/feedback"><input type="hidden" name="action" value="remove"><input type="hidden" name="message_id" value="{val(item.message_id)}"><input type="hidden" name="return_to" value="preferences"><button class="button subtle">Remove</button></form></td></tr>' for item in feedback) or '<tr><td colspan="6" class="muted">No explicit feedback yet.</td></tr>'
    body = f'''<section class="panel"><div class="section-head"><div><h2>How I work</h2><p>These local preferences override environment defaults when filled in. Drafts stay review-only.</p></div></div><form method="post" action="/preferences" class="grid"><div><label>Tone</label><input name="tone" maxlength="500" value="{val(profile.tone)}" placeholder="Use environment default"></div><div><label>Preferred reply length</label><select name="reply_length"><option value="">Environment default</option><option value="brief"{_selected(profile.reply_length, "brief")}>Brief</option><option value="standard"{_selected(profile.reply_length, "standard")}>Standard</option><option value="detailed"{_selected(profile.reply_length, "detailed")}>Detailed</option></select></div><div><label>Greeting</label><input name="greeting" maxlength="200" value="{val(profile.greeting)}" placeholder="Use environment default"></div><div><label>Closing</label><input name="closing" maxlength="200" value="{val(profile.closing)}" placeholder="Use environment default"></div><div><label>Signature</label><textarea name="signature" maxlength="1000" placeholder="Use environment default">{escape(profile.signature)}</textarea></div><div><label>Draft proactivity</label><select name="draft_proactivity"><option value="">Balanced (environment-safe policy)</option><option value="balanced"{_selected(profile.draft_proactivity, "balanced")}>Balanced</option><option value="conservative"{_selected(profile.draft_proactivity, "conservative")}>Conservative: action email + at least 80% confidence</option></select><p class="muted">Hard no-reply, marketing/spam, and no-send safeguards always win.</p></div><div><button class="button">Save work style</button></div></form></section><section class="panel"><div class="section-head"><div><h2>Learned reply preferences</h2><p>Derived only from the feedback below.</p></div></div><table><thead><tr><th>Sender</th><th>Category</th><th>Positive</th><th>Negative weight</th><th>Negative share</th><th>Rule</th></tr></thead><tbody>{_preference_rows(preferences)}</tbody></table></section><section class="panel"><h2>Underlying feedback</h2><p class="muted">Remove an individual decision to reverse its effect immediately.</p><table><thead><tr><th>When</th><th>Sender</th><th>Category</th><th>Decision</th><th>Note</th><th></th></tr></thead><tbody>{feedback_rows}</tbody></table></section>'''
    return _layout("preferences", "Preferences", "Inspect the transparent reply rules the assistant has learned from your explicit feedback.", body, ((len(preferences), "rules"), (sum(item.suppress_drafts for item in preferences), "suppressions")))


def render_folders(profiles: list[FolderProfile]) -> str:
    cards = "".join(f'<article class="folder-card"><small>{profile.examples_seen} sample(s)</small><h3>{escape(profile.folder_name)}</h3><p>{escape(profile.purpose)}</p><div>{"".join(f"<span class=\"topic\">{escape(topic)}</span>" for topic in profile.topics) or "<span class=\"muted\">No topics captured</span>"}</div></article>' for profile in profiles) or '<div class="panel empty-state"><strong>No folder profiles yet.</strong>Run <code>learn-folder-profiles</code> to build local folder summaries.</div>'
    return _layout("folders", "Folder intelligence", "Review the compact folder-purpose profiles used for safe filing suggestions. Messages are never moved.", f'<section class="grid">{cards}</section>', ((len(profiles), "profiles"), (sum(item.examples_seen for item in profiles), "samples")))


def render_settings(settings: Settings) -> str:
    values = (("Schedule", ", ".join(settings.schedules) or "Not configured"), ("Lookback window", f"{settings.lookback_hours} hours"), ("Digest", settings.digest_mode), ("Draft tone", settings.draft_tone), ("Folder suggestions", "Enabled" if settings.folder_suggestions_enabled else "Disabled"), ("Semantic suggestions", "Enabled" if settings.folder_semantic_suggestions_enabled else "Disabled"), ("No-reply safeguards", f"{len(settings.no_reply_senders)} address(es), {len(settings.no_reply_domains)} domain(s)"), ("Local storage", settings.database_path.name))
    cards = "".join(f'<div class="setting"><small>{escape(label)}</small><strong>{escape(value)}</strong></div>' for label, value in values)
    body = f'<section class="panel"><div class="section-head"><div><h2>Active local configuration</h2><p>This page is read-only in the pilot. It never shows credentials, API keys, or token data.</p></div></div><div class="grid">{cards}</div></section><section class="panel"><h2>Safety boundary</h2><p class="muted">This app uses delegated mailbox access. There is no Mail.Send permission or automatic move/delete behavior. Change configuration in your local <code>.env</code> file, then restart the relevant command.</p></section>'
    return _layout("settings", "Settings", "Review the active pilot configuration and the safeguards currently in effect.", body, ((len(settings.schedules), "daily times"), (settings.lookback_hours, "hours scanned")))


def serve_dashboard(settings: Settings, port: int = 8765) -> None:
    """Serve only on loopback; each request uses a short-lived SQLite connection."""
    class Handler(BaseHTTPRequestHandler):
        def _render(self, view: str, filters: dict[str, str] | None = None, notice: str = "") -> None:
            store = Store(settings.database_path)
            try:
                if view == "dashboard":
                    page = render_dashboard(store.list_processed_messages(status="review"), store.list_reply_preferences(), store.list_runs(), notice=notice)
                elif view == "activity": page = render_activity(store.list_runs(), store.list_processed_messages())
                elif view == "preferences": page = render_preferences(store.list_reply_preferences(), store.get_work_style(), store.list_feedback())
                elif view == "folders": page = render_folders(store.get_folder_profiles())
                else: page = render_settings(settings)
            finally: store.close()
            encoded = page.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path); routes = {"/": "dashboard", "/activity": "activity", "/preferences": "preferences", "/folders": "folders", "/settings": "settings"}
            view = routes.get(parsed.path)
            if not view: self.send_error(404); return
            query = parse_qs(parsed.query); filters = {key: query.get(key, [""])[0] for key in ("category", "status", "search")}
            self._render(view, filters, "Feedback saved." if query.get("saved") else "")

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/preferences":
                values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
                store = Store(settings.database_path)
                try:
                    store.save_work_style(WorkStyleProfile(*(values.get(key, [""])[0] for key in ("tone", "reply_length", "greeting", "closing", "signature", "draft_proactivity"))))
                except ValueError as error:
                    self._render("preferences", notice=f"Preferences were not saved: {error}"); return
                finally: store.close()
                self.send_response(303); self.send_header("Location", "/preferences?saved=1"); self.end_headers(); return
            if self.path != "/feedback": self.send_error(404); return
            values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()); message_id = values.get("message_id", [""])[0]; kind = values.get("feedback_type", [""])[0]
            store = Store(settings.database_path)
            try:
                if values.get("action", [""])[0] == "remove": store.remove_feedback(message_id)
                elif kind in FEEDBACK_TYPES: store.record_feedback(message_id, kind, values.get("note", [""])[0])
                else: self._render("dashboard", notice="Feedback was not saved: choose an outcome first."); return
            except ValueError as error: self._render("dashboard", notice=f"Feedback was not saved: {error}"); return
            finally: store.close()
            destination = "/preferences" if values.get("return_to", [""])[0] == "preferences" else "/"
            self.send_response(303); self.send_header("Location", destination + "?saved=1"); self.end_headers()

        def log_message(self, format: str, *args: object) -> None: return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler); print(f"Email Manager dashboard: http://127.0.0.1:{port} (localhost only; press Ctrl+C to stop)")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nDashboard stopped.")
    finally: server.server_close()
