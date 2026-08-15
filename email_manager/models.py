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


@dataclass(frozen=True)
class FeedbackRecord:
    """An explicit, locally recorded user decision about one source message."""

    message_id: str
    feedback_type: str
    sender_email: str
    category: str
    had_draft: bool
    note: str
    recorded_at: str


@dataclass(frozen=True)
class ReplyPreference:
    """Inspectable aggregate of feedback for a sender and message category."""

    sender_email: str
    category: str
    positive_examples: int
    negative_weight: int
    confidence: float
    suppress_drafts: bool


@dataclass(frozen=True)
class WorkStyleProfile:
    """User-owned local drafting preferences, with environment defaults as fallback."""

    tone: str = ""
    reply_length: str = ""
    greeting: str = ""
    closing: str = ""
    signature: str = ""
    draft_proactivity: str = ""


@dataclass(frozen=True)
class ProcessedMessage:
    """A review-safe summary of a locally processed source message."""

    message_id: str
    processed_at: str
    sender_email: str
    subject: str
    category: str
    needs_response: bool
    needs_action: bool
    has_draft: bool
    summary: str
    suggested_folder: str | None
    source_web_link: str
    draft_web_link: str = ""
    draft_reason: str = ""
    priority: str = "medium"
    action_items: tuple[str, ...] = ()
    suggested_followup_time: str = "none"
    confidence: float = 0.0
    rationale: str = ""
    feedback_type: str | None = None
    feedback_note: str = ""


@dataclass(frozen=True)
class RunHistory:
    run_at: str
    processed: int
    drafts_created: int
    skipped: int
    errors: int


@dataclass
class RunResult:
    processed: int = 0
    drafts_created: int = 0
    skipped: int = 0
    errors: int = 0
    action_items: list[tuple[Email, Assessment, FolderSuggestion | None]] = field(default_factory=list)
