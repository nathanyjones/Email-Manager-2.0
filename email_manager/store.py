"""SQLite persistence for processing state and bounded contact profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .models import Assessment, ContactProfile, FolderSuggestion


class Store:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL,
                category TEXT NOT NULL,
                needs_response INTEGER NOT NULL,
                needs_action INTEGER NOT NULL,
                draft_id TEXT,
                summary TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS folder_observations (
                sender_email TEXT NOT NULL,
                folder_id TEXT NOT NULL,
                folder_name TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                PRIMARY KEY (sender_email, folder_id)
            );
            CREATE TABLE IF NOT EXISTS contact_profiles (
                email TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                relationship_notes TEXT NOT NULL DEFAULT '',
                style_notes TEXT NOT NULL DEFAULT '',
                recurring_topics TEXT NOT NULL DEFAULT '[]',
                response_preferences TEXT NOT NULL DEFAULT '',
                examples_seen INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(processed_messages)")}
        if "suggested_folder" not in columns:
            self.connection.execute("ALTER TABLE processed_messages ADD COLUMN suggested_folder TEXT")
        self.connection.commit()

    def was_processed(self, message_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone() is not None

    def record_processed(self, message_id: str, assessment: Assessment, draft_id: str | None, suggested_folder: str | None = None) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO processed_messages
               (message_id, processed_at, category, needs_response, needs_action, draft_id, summary, suggested_folder)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, datetime.now(timezone.utc).isoformat(), assessment.category,
             assessment.needs_response, assessment.needs_action, draft_id, assessment.summary, suggested_folder),
        )
        self.connection.commit()

    def replace_folder_observations(self, observations: dict[tuple[str, str, str], int]) -> None:
        """Replace learned sender-to-folder counts from a fresh folder scan."""
        self.connection.execute("DELETE FROM folder_observations")
        self.connection.executemany(
            "INSERT INTO folder_observations (sender_email, folder_id, folder_name, message_count) VALUES (?, ?, ?, ?)",
            [(sender, folder_id, folder_name, count) for (sender, folder_id, folder_name), count in observations.items()],
        )
        self.connection.commit()

    def suggest_folder(self, sender_email: str, minimum_examples: int, minimum_confidence: float) -> FolderSuggestion | None:
        rows = self.connection.execute(
            """SELECT folder_id, folder_name, message_count FROM folder_observations
               WHERE sender_email = ? ORDER BY message_count DESC, folder_name ASC""",
            (sender_email.lower(),),
        ).fetchall()
        if not rows:
            return None
        total = sum(row["message_count"] for row in rows)
        best = rows[0]
        confidence = best["message_count"] / total
        if best["message_count"] < minimum_examples or confidence < minimum_confidence:
            return None
        return FolderSuggestion(best["folder_id"], best["folder_name"], best["message_count"], confidence)

    def get_profile(self, email: str) -> ContactProfile:
        row = self.connection.execute("SELECT * FROM contact_profiles WHERE email = ?", (email.lower(),)).fetchone()
        if not row:
            return ContactProfile(email=email.lower())
        return ContactProfile(
            email=row["email"], display_name=row["display_name"],
            relationship_notes=row["relationship_notes"], style_notes=row["style_notes"],
            recurring_topics=tuple(json.loads(row["recurring_topics"])),
            response_preferences=row["response_preferences"], examples_seen=row["examples_seen"],
        )

    def save_profile(self, profile: ContactProfile) -> None:
        self.connection.execute(
            """INSERT INTO contact_profiles
               (email, display_name, relationship_notes, style_notes, recurring_topics, response_preferences, examples_seen, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET display_name=excluded.display_name,
               relationship_notes=excluded.relationship_notes, style_notes=excluded.style_notes,
               recurring_topics=excluded.recurring_topics, response_preferences=excluded.response_preferences,
               examples_seen=excluded.examples_seen, updated_at=excluded.updated_at""",
            (profile.email.lower(), profile.display_name, profile.relationship_notes, profile.style_notes,
             json.dumps(profile.recurring_topics), profile.response_preferences, profile.examples_seen,
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
