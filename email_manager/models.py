"""Shared data objects for the pilot."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Email:
    id: str
    subject: str
    sender_name: str
    sender_email: str
    received_at: str
    body_preview: str
    body: str
    to_recipients: tuple[str, ...] = ()
    cc_recipients: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    web_link: str = ""


@dataclass(frozen=True)
class Assessment:
    needs_response: bool
    needs_action: bool
    priority: str
    category: str
    summary: str
    action_items: tuple[str, ...]
    draft_reply: str | None
    suggested_followup_time: str
    confidence: float
    rationale: str = ""


@dataclass(frozen=True)
class ContactProfile:
    email: str
    display_name: str = ""
    relationship_notes: str = ""
    style_notes: str = ""
    recurring_topics: tuple[str, ...] = ()
    response_preferences: str = ""
    examples_seen: int = 0


@dataclass(frozen=True)
class MailFolder:
    id: str
    display_name: str


@dataclass(frozen=True)
class FolderSuggestion:
    folder_id: str
    folder_name: str
    examples: int
    confidence: float
    source: str = "sender-history"


@dataclass(frozen=True)
class FolderProfile:
    folder_id: str
    folder_name: str
    purpose: str
    topics: tuple[str, ...]
    participant_signals: tuple[str, ...]
    examples_seen: int


@dataclass
class RunResult:
    processed: int = 0
    drafts_created: int = 0
    skipped: int = 0
    errors: int = 0
    action_items: list[tuple[Email, Assessment, FolderSuggestion | None]] = field(default_factory=list)
