"""Scheduled triage workflow. This module never sends a reply to a source email."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .ai import EmailAssistant
from .config import Settings
from .graph import GraphClient
from .models import Assessment, ContactProfile, Email, FolderSuggestion, MailFolder, RunResult, WorkStyleProfile
from .store import Store


CATEGORY_LABELS = {
    "action": "AI: Action needed",
    "informational": "AI: Informational",
    "marketing": "AI: Marketing",
    "spam": "AI: Spam review",
}

AUTOMATED_LOCAL_PART_MARKERS = ("no-reply", "noreply", "do-not-reply", "donotreply", "mailer-daemon")
SPECIAL_FOLDERS = {"inbox", "drafts", "sent items", "deleted items", "junk email", "archive", "outbox", "conversation history"}


class EmailManager:
    def __init__(self, settings: Settings, graph: GraphClient, assistant: EmailAssistant, store: Store) -> None:
        self.settings, self.graph, self.assistant, self.store = settings, graph, assistant, store

    def run(self, now: datetime | None = None) -> RunResult:
        now = now or datetime.now(timezone.utc)
        for index, label in enumerate(CATEGORY_LABELS.values()):
            self.graph.ensure_category(label, f"preset{index}")
        messages = self.graph.list_recent_messages(now - timedelta(hours=self.settings.lookback_hours))
        result = RunResult()
        for email in messages:
            if self.store.was_processed(email.id) or self._excluded(email):
                result.skipped += 1
                continue
            try:
                assessment, draft_created, suggestion = self.process_message(email.id, email)
                result.drafts_created += int(draft_created)
                if assessment.needs_action:
                    result.action_items.append((email, assessment, suggestion))
                result.processed += 1
            except Exception as error:  # preserve the message for the next scheduled retry
                result.errors += 1
                print(f"Failed to process {email.id}: {error}")
        self._create_digest(result, now)
        self.store.record_run(now.isoformat(), result.processed, result.drafts_created, result.skipped, result.errors)
        return result

    def process_message(self, message_id: str, email: Email | None = None) -> tuple[Assessment, bool, FolderSuggestion | None]:
        """Idempotently process one source message, for a schedule or explicit panel click."""
        if self.store.was_processed(message_id):
            existing = self.store.get_processed_message(message_id)
            if existing is None:  # defensive; was_processed and read share one table
                raise RuntimeError("Could not load existing local decision")
            return Assessment(existing.needs_response, existing.needs_action, existing.priority, existing.category,
                              existing.summary, existing.action_items, None, existing.suggested_followup_time,
                              existing.confidence, existing.rationale), False, None
        email = email or self.graph.get_message(message_id)
        if email is None:
            raise ValueError("This email is no longer available in the signed-in mailbox.")
        if self._excluded(email):
            raise ValueError("This sender is excluded from local Email Manager processing.")
        stored_profile = self.store.get_profile(email.sender_email)
        profile = stored_profile
        feedback_context = self.store.feedback_summary(email.sender_email)
        if feedback_context:
            profile = ContactProfile(profile.email, profile.display_name, profile.relationship_notes, profile.style_notes,
                                     profile.recurring_topics, "\n".join(filter(None, (profile.response_preferences, feedback_context))), profile.examples_seen)
        style = self._work_style()
        assessment = self.assistant.assess(email, profile, style.tone)
        self.graph.categorize(email, CATEGORY_LABELS[assessment.category], flagged=assessment.needs_action)
        suggestion = self._suggest_folder(email)
        if suggestion:
            self.graph.ensure_category(self._suggestion_category(suggestion), "preset6")
            self.graph.categorize(email, self._suggestion_category(suggestion), flagged=assessment.needs_action)
        draft_reason = self._draft_reason(email, assessment, style)
        draft_id, draft_web_link = None, ""
        if draft_reason == "draft_created":
            draft_id, draft_web_link = self.graph.create_reply_draft(email.id, self._format_draft(email, assessment.draft_reply or "", style))
        self.store.record_processed(email, assessment, draft_id, suggestion.folder_name if suggestion else None, draft_web_link, draft_reason)
        self._learn_from_assessment(email, assessment, stored_profile)
        return assessment, bool(draft_id), suggestion

    def _excluded(self, email: Email) -> bool:
        return email.sender_email.lower() in self.settings.excluded_senders

    def _draft_reason(self, email: Email, assessment: Assessment, style: WorkStyleProfile | None = None) -> str:
        if not assessment.needs_response:
            return "AI assessed that no personal reply is needed."
        if not assessment.draft_reply:
            return "AI did not have enough safe context to prepare a reply."
        if assessment.category in {"marketing", "spam"}:
            return "Replies are disabled for marketing and spam."
        sender = email.sender_email.lower()
        if sender in self.settings.no_reply_senders:
            return "Replies are blocked for this configured no-reply sender."
        local_part, _, domain = sender.partition("@")
        if domain in self.settings.no_reply_domains or any(marker in local_part for marker in AUTOMATED_LOCAL_PART_MARKERS):
            return "Replies are blocked for automated or no-reply senders."
        if self.store.reply_preference(sender, assessment.category).suppress_drafts:
            return "Your explicit feedback suppresses drafts for this sender and category."
        if (style or self._work_style()).draft_proactivity == "conservative" and (assessment.category != "action" or assessment.confidence < 0.80):
            return "Your conservative profile drafts only action emails with at least 0.80 response confidence."
        return "draft_created"

    def _work_style(self) -> WorkStyleProfile:
        saved = self.store.get_work_style()
        length = saved.reply_length or "standard"
        length_note = {"brief": "Keep the reply brief (about 2–4 sentences).", "standard": "Use a concise, complete reply.", "detailed": "Use a thorough but focused reply."}[length]
        return WorkStyleProfile(f"{saved.tone or self.settings.draft_tone}. {length_note}", length, saved.greeting or self.settings.draft_greeting,
                                saved.closing or self.settings.draft_closing, saved.signature or self.settings.draft_signature,
                                saved.draft_proactivity or "balanced")

    def _format_draft(self, email: Email, draft_body: str, style: WorkStyleProfile | None = None) -> str:
        style = style or self._work_style()
        lines: list[str] = []
        if style.greeting:
            recipient = email.sender_name.strip() or "there"
            lines.extend((f"{style.greeting} {recipient},", ""))
        lines.append(draft_body.strip())
        if style.closing:
            lines.extend(("", style.closing))
        if style.signature:
            lines.append(style.signature)
        return "\n".join(lines)

    def _suggest_folder(self, email: Email) -> FolderSuggestion | None:
        if not self.settings.folder_suggestions_enabled:
            return None
        if self.settings.folder_semantic_suggestions_enabled:
            semantic = self.assistant.suggest_semantic_folder(email, self.store.get_folder_profiles())
            if semantic and semantic.confidence >= self.settings.folder_semantic_min_confidence:
                return semantic
        return self.store.suggest_folder(
            email.sender_email, self.settings.folder_min_examples, self.settings.folder_min_confidence
        )

    @staticmethod
    def _suggestion_category(suggestion: FolderSuggestion) -> str:
        return f"AI: Suggested — {suggestion.folder_name}"[:255]

    def _learn_from_assessment(self, email: Email, assessment: Assessment, profile: ContactProfile) -> None:
        topics = list(profile.recurring_topics)
        if assessment.category == "action" and email.subject not in topics:
            topics.append(email.subject[:120])
        self.store.save_profile(ContactProfile(
            email=email.sender_email, display_name=email.sender_name or profile.display_name,
            relationship_notes=profile.relationship_notes,
            style_notes=profile.style_notes,
            recurring_topics=tuple(topics[-10:]), response_preferences=profile.response_preferences,
            examples_seen=profile.examples_seen + 1,
        ))

    def _create_digest(self, result: RunResult, now: datetime) -> None:
        if self.settings.digest_mode == "disabled" or not result.action_items:
            return
        lines = ["AI Email Manager action digest", f"Generated: {now.astimezone().strftime('%Y-%m-%d %H:%M %Z')}", ""]
        for email, assessment, suggestion in result.action_items:
            lines.extend([
                f"[{assessment.priority.upper()}] {email.subject}",
                f"From: {email.sender_name} <{email.sender_email}>",
                f"Summary: {assessment.summary}",
                "Actions: " + ("; ".join(assessment.action_items) or "Review message"),
                f"Message: {email.web_link or 'Open Outlook to view'}", "",
            ])
            if suggestion:
                lines.insert(-1, f"Suggested folder: {suggestion.folder_name} ({suggestion.confidence:.0%} confidence)")
        subject = f"AI Email Manager: {len(result.action_items)} action item(s)"
        body = "\n".join(lines)
        self.graph.create_digest_draft(self.settings.digest_recipients, subject, body)

    def bootstrap_profiles(self, days: int = 30) -> int:
        """Build bounded contact profiles from sent-mail metadata without generating or changing messages."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        sent = self.graph.list_recent_sent_messages(since)[:self.settings.max_history_messages]
        for email in sent:
            for recipient in email.to_recipients:
                if not recipient:
                    continue
                profile = self.store.get_profile(recipient)
                topics = list(profile.recurring_topics)
                if email.subject not in topics:
                    topics.append(email.subject[:120])
                self.store.save_profile(ContactProfile(
                    email=recipient, display_name=profile.display_name,
                    relationship_notes=profile.relationship_notes,
                    style_notes=profile.style_notes or "Concise professional tone inferred from sent-mail review.",
                    recurring_topics=tuple(topics[-10:]), response_preferences=profile.response_preferences,
                    examples_seen=profile.examples_seen + 1,
                ))
        return len(sent)

    def learn_folders(self) -> tuple[int, int]:
        """Learn sender-to-folder tendencies from existing filed messages; never moves mail."""
        observations: dict[tuple[str, str, str], int] = {}
        folders = self.graph.list_user_folders()
        scanned_messages = 0
        excluded = {name.lower() for name in self.settings.excluded_folders}
        for folder in folders:
            if folder.display_name.lower() in SPECIAL_FOLDERS or folder.display_name.lower() in excluded:
                continue
            for email in self.graph.list_folder_messages(folder.id, self.settings.folder_history_per_folder):
                if not email.sender_email:
                    continue
                key = (email.sender_email.lower(), folder.id, folder.display_name)
                observations[key] = observations.get(key, 0) + 1
                scanned_messages += 1
        self.store.replace_folder_observations(observations)
        return len(folders), scanned_messages

    def learn_folder_profiles(self) -> tuple[int, int]:
        """Build semantic folder profiles from bounded samples; never changes mailbox messages."""
        profiles = []
        scanned_messages = 0
        excluded = {name.lower() for name in self.settings.excluded_folders}
        for folder in self.graph.list_user_folders():
            if folder.display_name.lower() in SPECIAL_FOLDERS or folder.display_name.lower() in excluded:
                continue
            emails = self.graph.list_folder_messages(folder.id, self.settings.folder_profile_samples)
            if not emails:
                continue
            profiles.append(self.assistant.build_folder_profile(folder.display_name, folder.id, emails))
            scanned_messages += len(emails)
        self.store.replace_folder_profiles(profiles)
        return len(profiles), scanned_messages

    def refresh_dashboard_metadata(self, limit: int = 500) -> tuple[int, int]:
        """Backfill legacy dashboard fields from Graph without changing any messages."""
        updated = 0
        unavailable = 0
        for message_id in self.store.dashboard_metadata_gaps(limit):
            try:
                email = self.graph.get_message(message_id)
                if email is None:
                    unavailable += 1
                    continue
                self.store.update_dashboard_metadata(email)
                updated += 1
            except Exception as error:
                unavailable += 1
                print(f"Could not refresh dashboard metadata for {message_id}: {error}")
        return updated, unavailable
