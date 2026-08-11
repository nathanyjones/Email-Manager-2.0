from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from email_manager.models import Assessment, Email, MailFolder
from email_manager.service import EmailManager
from email_manager.store import Store


EMAIL = Email(
    id="message-1", subject="Proposal needed", sender_name="Client", sender_email="client@example.com",
    received_at="2026-08-10T12:00:00Z", body_preview="Please send a proposal.", body="Please send a proposal.",
    web_link="https://outlook.example/message-1",
)
ASSESSMENT = Assessment(
    needs_response=True, needs_action=True, priority="high", category="action",
    summary="Client requested a proposal.", action_items=("Prepare proposal",),
    draft_reply="Thanks, I will prepare the proposal.", suggested_followup_time="today", confidence=0.9,
)


class FakeGraph:
    def __init__(self, emails, folders=(), folder_messages=None):
        self.emails = emails
        self.folders = folders
        self.folder_messages = folder_messages or {}
        self.categories = []
        self.drafts = []
        self.digest_drafts = []

    def ensure_category(self, name, color):
        self.categories.append(name)

    def list_recent_messages(self, since):
        return self.emails

    def categorize(self, email, category, flagged=False):
        self.categories.append((email.id, category, flagged))

    def create_reply_draft(self, email_id, body):
        self.drafts.append((email_id, body))
        return "draft-1"

    def create_digest_draft(self, recipients, subject, body):
        self.digest_drafts.append((recipients, subject, body))
        return "digest-1"

    def list_user_folders(self):
        return self.folders

    def list_folder_messages(self, folder_id, limit):
        return self.folder_messages.get(folder_id, [])[:limit]


class FakeAssistant:
    def __init__(self, assessment=ASSESSMENT):
        self.assessment = assessment

    def assess(self, email, profile, draft_tone):
        return self.assessment


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.store = Store(Path(self.tempdir.name) / "test.db")
        self.settings = SimpleNamespace(
            lookback_hours=24, excluded_senders=(), digest_mode="draft",
            digest_recipients=("owner@example.com",), max_history_messages=40,
            draft_tone="concise", draft_greeting="Hi", draft_closing="Best,",
            draft_signature="Owner", no_reply_senders=(), no_reply_domains=(),
            folder_suggestions_enabled=True, folder_history_per_folder=50,
            folder_min_examples=2, folder_min_confidence=0.8, excluded_folders=(),
        )

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def test_creates_category_reply_and_digest_once(self):
        graph = FakeGraph([EMAIL])
        manager = EmailManager(self.settings, graph, FakeAssistant(), self.store)

        first = manager.run(datetime(2026, 8, 10, tzinfo=timezone.utc))
        second = manager.run(datetime(2026, 8, 10, 1, tzinfo=timezone.utc))

        self.assertEqual((first.processed, first.drafts_created, first.errors), (1, 1, 0))
        self.assertEqual(len(graph.drafts), 1)
        self.assertEqual(graph.drafts[0][1], "Hi Client,\n\nThanks, I will prepare the proposal.\n\nBest,\nOwner")
        self.assertIn((EMAIL.id, "AI: Action needed", True), graph.categories)
        self.assertEqual(len(graph.digest_drafts), 1)
        self.assertEqual((second.processed, second.skipped), (0, 1))

    def test_excluded_sender_is_untouched(self):
        self.settings.excluded_senders = ("client@example.com",)
        graph = FakeGraph([EMAIL])
        result = EmailManager(self.settings, graph, FakeAssistant(), self.store).run()
        self.assertEqual((result.processed, result.skipped), (0, 1))
        self.assertEqual(graph.drafts, [])

    def test_model_failure_leaves_message_unprocessed_for_retry(self):
        class FailingAssistant:
            def assess(self, email, profile, draft_tone):
                raise ValueError("invalid model response")

        graph = FakeGraph([EMAIL])
        result = EmailManager(self.settings, graph, FailingAssistant(), self.store).run()
        self.assertEqual((result.processed, result.errors), (0, 1))
        self.assertFalse(self.store.was_processed(EMAIL.id))

    def test_no_reply_sender_gets_no_draft_but_remains_an_action(self):
        automated = Email(
            id="message-2", subject="Security alert", sender_name="Security", sender_email="no-reply@example.com",
            received_at="2026-08-10T12:00:00Z", body_preview="Review this alert.", body="Review this alert.",
        )
        graph = FakeGraph([automated])
        result = EmailManager(self.settings, graph, FakeAssistant(), self.store).run()
        self.assertEqual((result.processed, result.drafts_created), (1, 0))
        self.assertEqual(graph.drafts, [])
        self.assertEqual(len(graph.digest_drafts), 1)

    def test_learned_folder_adds_a_suggestion_category_without_moving_mail(self):
        stored_messages = [
            Email(id=f"filed-{index}", subject="Proposal", sender_name="Client", sender_email="client@example.com",
                  received_at="2026-08-01T12:00:00Z", body_preview="", body="")
            for index in range(2)
        ]
        graph = FakeGraph([], folders=(MailFolder("projects", "Projects"),), folder_messages={"projects": stored_messages})
        manager = EmailManager(self.settings, graph, FakeAssistant(), self.store)
        self.assertEqual(manager.learn_folders(), (1, 2))

        graph.emails = [EMAIL]
        result = manager.run()

        self.assertEqual(result.processed, 1)
        self.assertIn((EMAIL.id, "AI: Suggested — Projects", True), graph.categories)
        self.assertEqual(len(graph.drafts), 1)


if __name__ == "__main__":
    unittest.main()
