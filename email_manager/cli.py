"""Command line interface for the email-manager pilot."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import time

from .ai import EmailAssistant
from .config import Settings
from .dashboard import serve_dashboard
from .graph import GraphClient
from .outlook_addin import serve_outlook_addin
from .service import EmailManager
from .store import Store


def _manager() -> tuple[EmailManager, Store]:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    return EmailManager(settings, GraphClient(settings.client_id, settings.token_cache_path), EmailAssistant(settings.openai_api_key, settings.model), store), store


def _run_once() -> None:
    manager, store = _manager()
    try:
        result = manager.run()
        print(f"Processed {result.processed}; created {result.drafts_created} reply draft(s); skipped {result.skipped}; errors {result.errors}.")
    finally:
        store.close()


def _serve() -> None:
    settings = Settings.from_env()
    schedules = set(settings.schedules)
    last_run_minute: str | None = None
    print("Email Manager scheduler running at: " + ", ".join(settings.schedules))
    while True:
        now = datetime.now().astimezone()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        if now.strftime("%H:%M") in schedules and stamp != last_run_minute:
            _run_once()
            last_run_minute = stamp
        time.sleep(20)


def _record_feedback(message_id: str, feedback_type: str, note: str) -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    try:
        feedback = store.record_feedback(message_id, feedback_type, note)
        print(f"Saved {feedback.feedback_type} feedback for {feedback.message_id} ({feedback.sender_email}, {feedback.category}).")
    finally:
        store.close()


def _list_feedback(sender: str | None, limit: int) -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    try:
        records = store.list_feedback(sender, limit)
        if not records:
            print("No local feedback records.")
            return
        for record in records:
            note = f" — {record.note}" if record.note else ""
            print(f"{record.recorded_at}\t{record.message_id}\t{record.feedback_type}\t{record.sender_email}\t{record.category}{note}")
    finally:
        store.close()


def _remove_feedback(message_id: str) -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    try:
        print("Removed feedback." if store.remove_feedback(message_id) else "No feedback record found.")
    finally:
        store.close()


def _show_feedback_summary() -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    try:
        preferences = store.list_reply_preferences()
        if not preferences:
            print("No local reply preferences.")
            return
        for preference in preferences:
            decision = "suppress drafts" if preference.suppress_drafts else "no suppression"
            print(f"{preference.sender_email}\t{preference.category}\tpositive={preference.positive_examples}\t"
                  f"negative_weight={preference.negative_weight}\tnegative_share={preference.confidence:.0%}\t{decision}")
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe Outlook email triage and drafting pilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Process recent email once")
    commands.add_parser("serve", help="Run at configured local schedule times")
    bootstrap = commands.add_parser("bootstrap-profiles", help="Build bounded recipient profiles from sent mail")
    bootstrap.add_argument("--days", type=int, default=30)
    commands.add_parser("learn-folders", help="Learn existing sender-to-folder patterns without moving mail")
    commands.add_parser("learn-folder-profiles", help="Build AI folder-purpose profiles from bounded samples; never moves mail")
    feedback = commands.add_parser("feedback", help="Record an explicit local reply-feedback decision")
    feedback.add_argument("message_id", help="Source message ID from local processing history")
    feedback.add_argument("feedback_type", choices=("draft_sent", "draft_edited", "draft_deleted", "manual_draft_requested", "never_draft_like_this"))
    feedback.add_argument("--note", default="", help="Optional local note (up to 1,000 characters)")
    feedback_list = commands.add_parser("feedback-list", help="Inspect locally recorded reply feedback")
    feedback_list.add_argument("--sender", help="Only show feedback for this sender")
    feedback_list.add_argument("--limit", type=int, default=100)
    feedback_remove = commands.add_parser("feedback-remove", help="Remove feedback for a source message")
    feedback_remove.add_argument("message_id")
    commands.add_parser("feedback-summary", help="Inspect the transparent aggregate reply-preference rules")
    refresh = commands.add_parser("refresh-dashboard-metadata", help="Read legacy source metadata for the local dashboard; never changes mail")
    refresh.add_argument("--limit", type=int, default=500)
    dashboard = commands.add_parser("dashboard", help="Start the local-only review dashboard")
    dashboard.add_argument("--port", type=int, default=8765)
    outlook = commands.add_parser("outlook-addin", help="Start the localhost-only HTTPS bridge for the private Outlook add-in")
    outlook.add_argument("--port", type=int, default=8766)
    outlook.add_argument("--certificate", required=True, help="Trusted localhost HTTPS certificate path")
    outlook.add_argument("--private-key", required=True, help="Matching HTTPS private-key path")
    args = parser.parse_args()
    if args.command == "run":
        _run_once()
    elif args.command == "serve":
        _serve()
    elif args.command == "bootstrap-profiles":
        manager, store = _manager()
        try:
            print(f"Reviewed {manager.bootstrap_profiles(args.days)} sent messages for contact profiles.")
        finally:
            store.close()
    elif args.command == "learn-folders":
        manager, store = _manager()
        try:
            folders, messages = manager.learn_folders()
            print(f"Scanned {messages} messages across {folders} folders; no messages were moved.")
        finally:
            store.close()
    elif args.command == "feedback":
        _record_feedback(args.message_id, args.feedback_type, args.note)
    elif args.command == "feedback-list":
        _list_feedback(args.sender, args.limit)
    elif args.command == "feedback-remove":
        _remove_feedback(args.message_id)
    elif args.command == "feedback-summary":
        _show_feedback_summary()
    elif args.command == "dashboard":
        if not 1024 <= args.port <= 65535:
            parser.error("dashboard --port must be between 1024 and 65535")
        serve_dashboard(Settings.from_env(), args.port)
    elif args.command == "outlook-addin":
        if not 1024 <= args.port <= 65535:
            parser.error("outlook-addin --port must be between 1024 and 65535")
        serve_outlook_addin(Settings.from_env(), args.port, Path(args.certificate), Path(args.private_key))
    elif args.command == "refresh-dashboard-metadata":
        if not 1 <= args.limit <= 500:
            parser.error("refresh-dashboard-metadata --limit must be between 1 and 500")
        manager, store = _manager()
        try:
            updated, unavailable = manager.refresh_dashboard_metadata(args.limit)
            print(f"Refreshed dashboard metadata for {updated} message(s); {unavailable} message(s) were unavailable. No messages were changed.")
        finally:
            store.close()
    else:
        manager, store = _manager()
        try:
            profiles, messages = manager.learn_folder_profiles()
            print(f"Built {profiles} semantic folder profile(s) from {messages} message samples; no messages were moved.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
