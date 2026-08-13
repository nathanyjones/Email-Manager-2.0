"""SQLite persistence for processing state and bounded contact profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .models import Assessment, ContactProfile, Email, FeedbackRecord, FolderProfile, FolderSuggestion, ReplyPreference


FEEDBACK_TYPES = {
    "draft_sent", "draft_edited", "draft_deleted", "manual_draft_requested", "never_draft_like_this",
}


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
            CREATE TABLE IF NOT EXISTS folder_profiles (
                folder_id TEXT PRIMARY KEY,
                folder_name TEXT NOT NULL,
                purpose TEXT NOT NULL,
                topics TEXT NOT NULL,
                participant_signals TEXT NOT NULL,
                examples_seen INTEGER NOT NULL,
                updated_at TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS feedback_records (
                message_id TEXT PRIMARY KEY,
                feedback_type TEXT NOT NULL,
                sender_email TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                had_draft INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            );
        """)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(processed_messages)")}
        if "suggested_folder" not in columns:
            self.connection.execute("ALTER TABLE processed_messages ADD COLUMN suggested_folder TEXT")
        if "sender_email" not in columns:
            self.connection.execute("ALTER TABLE processed_messages ADD COLUMN sender_email TEXT NOT NULL DEFAULT ''")
        if "subject" not in columns:
            self.connection.execute("ALTER TABLE processed_messages ADD COLUMN subject TEXT NOT NULL DEFAULT ''")
        self.connection.commit()

    def replace_folder_profiles(self, profiles: list[FolderProfile]) -> None:
        self.connection.execute("DELETE FROM folder_profiles")
        self.connection.executemany(
            """INSERT INTO folder_profiles
            (folder_id, folder_name, purpose, topics, participant_signals, examples_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(profile.folder_id, profile.folder_name, profile.purpose, json.dumps(profile.topics),
              json.dumps(profile.participant_signals), profile.examples_seen, datetime.now(timezone.utc).isoformat())
             for profile in profiles],
        )
        self.connection.commit()

    def get_folder_profiles(self) -> list[FolderProfile]:
        rows = self.connection.execute("SELECT * FROM folder_profiles ORDER BY folder_name").fetchall()
        return [FolderProfile(
            folder_id=row["folder_id"], folder_name=row["folder_name"], purpose=row["purpose"],
            topics=tuple(json.loads(row["topics"])), participant_signals=tuple(json.loads(row["participant_signals"])),
            examples_seen=row["examples_seen"],
        ) for row in rows]

    def was_processed(self, message_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone() is not None

    def record_processed(self, email: Email, assessment: Assessment, draft_id: str | None, suggested_folder: str | None = None) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO processed_messages
               (message_id, processed_at, category, needs_response, needs_action, draft_id, summary, suggested_folder, sender_email, subject)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (email.id, datetime.now(timezone.utc).isoformat(), assessment.category,
             assessment.needs_response, assessment.needs_action, draft_id, assessment.summary, suggested_folder,
             email.sender_email.lower(), email.subject[:500]),
        )
        self.connection.commit()

    def record_feedback(self, message_id: str, feedback_type: str, note: str = "") -> FeedbackRecord:
        """Save or replace the user's current explicit decision for a processed message."""
        if feedback_type not in FEEDBACK_TYPES:
            raise ValueError(f"feedback_type must be one of: {', '.join(sorted(FEEDBACK_TYPES))}")
        source = self.connection.execute(
            "SELECT sender_email, category, draft_id FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if not source:
            raise ValueError("Message is not in local processing history; feedback must reference a processed source message")
        recorded_at = datetime.now(timezone.utc).isoformat()
        clean_note = note.strip()[:1000]
        self.connection.execute(
            """INSERT INTO feedback_records
               (message_id, feedback_type, sender_email, category, had_draft, note, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET feedback_type=excluded.feedback_type,
               sender_email=excluded.sender_email, category=excluded.category, had_draft=excluded.had_draft,
               note=excluded.note, recorded_at=excluded.recorded_at""",
            (message_id, feedback_type, source["sender_email"].lower(), source["category"],
             bool(source["draft_id"]), clean_note, recorded_at),
        )
        self.connection.commit()
        return FeedbackRecord(message_id, feedback_type, source["sender_email"].lower(), source["category"],
                              bool(source["draft_id"]), clean_note, recorded_at)

    def list_feedback(self, sender_email: str | None = None, limit: int = 100) -> list[FeedbackRecord]:
        query = "SELECT * FROM feedback_records"
        values: list[object] = []
        if sender_email:
            query += " WHERE sender_email = ?"
            values.append(sender_email.lower())
        query += " ORDER BY recorded_at DESC LIMIT ?"
        values.append(limit)
        return [FeedbackRecord(
            message_id=row["message_id"], feedback_type=row["feedback_type"], sender_email=row["sender_email"],
            category=row["category"], had_draft=bool(row["had_draft"]), note=row["note"], recorded_at=row["recorded_at"],
        ) for row in self.connection.execute(query, values).fetchall()]

    def remove_feedback(self, message_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM feedback_records WHERE message_id = ?", (message_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def reply_preference(self, sender_email: str, category: str) -> ReplyPreference:
        """Return a conservative, category-specific draft-suppression signal.

        A deletion counts as one negative signal and an explicit "never draft" as two.
        Suppression needs at least two negative points and 75% negative feedback, so one
        accidental deletion cannot silently change future behavior.
        """
        rows = self.connection.execute(
            "SELECT feedback_type FROM feedback_records WHERE sender_email = ? AND category = ?",
            (sender_email.lower(), category),
        ).fetchall()
        positive = sum(row["feedback_type"] in {"draft_sent", "draft_edited", "manual_draft_requested"} for row in rows)
        negative = sum(2 if row["feedback_type"] == "never_draft_like_this" else 1
                       for row in rows if row["feedback_type"] in {"draft_deleted", "never_draft_like_this"})
        total = positive + negative
        confidence = negative / total if total else 0.0
        return ReplyPreference(sender_email.lower(), category, positive, negative, confidence,
                               negative >= 2 and confidence >= 0.75)

    def feedback_summary(self, sender_email: str) -> str:
        """A compact, explicit signal for the assessor; no feedback means no added policy."""
        rows = self.connection.execute(
            "SELECT feedback_type FROM feedback_records WHERE sender_email = ?", (sender_email.lower(),)
        ).fetchall()
        if not rows:
            return ""
        positive = sum(row["feedback_type"] in {"draft_sent", "draft_edited", "manual_draft_requested"} for row in rows)
        negative = sum(row["feedback_type"] in {"draft_deleted", "never_draft_like_this"} for row in rows)
        return f"Explicit local feedback for this sender: {positive} reply-positive and {negative} reply-negative decisions."

    def list_reply_preferences(self) -> list[ReplyPreference]:
        pairs = self.connection.execute(
            "SELECT DISTINCT sender_email, category FROM feedback_records ORDER BY sender_email, category"
        ).fetchall()
        return [self.reply_preference(row["sender_email"], row["category"]) for row in pairs]

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
