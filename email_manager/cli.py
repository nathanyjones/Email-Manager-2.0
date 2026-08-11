"""Command line interface for the email-manager pilot."""

from __future__ import annotations

import argparse
from datetime import datetime
import time

from .ai import EmailAssistant
from .config import Settings
from .graph import GraphClient
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe Outlook email triage and drafting pilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Process recent email once")
    commands.add_parser("serve", help="Run at configured local schedule times")
    bootstrap = commands.add_parser("bootstrap-profiles", help="Build bounded recipient profiles from sent mail")
    bootstrap.add_argument("--days", type=int, default=30)
    commands.add_parser("learn-folders", help="Learn existing sender-to-folder patterns without moving mail")
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
    else:
        manager, store = _manager()
        try:
            folders, messages = manager.learn_folders()
            print(f"Scanned {messages} messages across {folders} folders; no messages were moved.")
        finally:
            store.close()


if __name__ == "__main__":
    main()
