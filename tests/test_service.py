from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from email_manager.models import Assessment, Email, FolderProfile, FolderSuggestion, MailFolder, WorkStyleProfile
from email_manager.dashboard import render_activity, render_dashboard, render_folders, render_preferences, render_settings
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
        return "draft-1", f"https://outlook.example/drafts/{email_id}"

    def create_digest_draft(self, recipients, subject, body):
        self.digest_drafts.append((recipients, subject, body))
        return "digest-1"

    def list_user_folders(self):
        return self.folders

    def list_folder_messages(self, folder_id, limit):
        return self.folder_messages.get(folder_id, [])[:limit]

    def get_message(self, message_id):
        return next(email for email in self.emails if email.id == message_id)


class FakeAssistant:
    def __init__(self, assessment=ASSESSMENT):
        self.assessment = assessment

    def assess(self, email, profile, draft_tone):
        return self.assessment

    def build_folder_profile(self, folder_name, folder_id, emails):
        return FolderProfile(folder_id, folder_name, f"{folder_name} correspondence", (folder_name,), (), len(emails))

    def suggest_semantic_folder(self, email, folder_profiles):
        return None


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
            folder_semantic_suggestions_enabled=False, folder_profile_samples=10,
            folder_semantic_min_confidence=0.75,
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
        self.assertEqual(len(self.store.list_runs()), 2)
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

    def test_semantic_profile_can_suggest_folder_for_a_mixed_sender(self):
        stored_messages = [
            Email(id="project-1", subject="Proposal for new site", sender_name="Client", sender_email="client@example.com",
                  received_at="2026-08-01T12:00:00Z", body_preview="New project proposal scope", body="New project proposal scope")
        ]
        graph = FakeGraph([], folders=(MailFolder("projects", "Projects"),), folder_messages={"projects": stored_messages})

        class SemanticAssistant(FakeAssistant):
            def suggest_semantic_folder(self, email, folder_profiles):
                return FolderSuggestion("projects", "Projects", 1, 0.92, "semantic")

        manager = EmailManager(self.settings, graph, SemanticAssistant(), self.store)
        self.assertEqual(manager.learn_folder_profiles(), (1, 1))
        self.settings.folder_semantic_suggestions_enabled = True
        graph.emails = [EMAIL]
        manager.run()
        self.assertIn((EMAIL.id, "AI: Suggested — Projects", True), graph.categories)

    def test_low_confidence_semantic_suggestion_does_not_override_sender_history(self):
        self.store.replace_folder_profiles([FolderProfile("projects", "Projects", "Project work", (), (), 2)])

        class LowConfidenceAssistant(FakeAssistant):
            def suggest_semantic_folder(self, email, folder_profiles):
                return FolderSuggestion("projects", "Projects", 2, 0.40, "semantic")

        self.settings.folder_semantic_suggestions_enabled = True
        result = EmailManager(self.settings, FakeGraph([EMAIL]), LowConfidenceAssistant(), self.store).run()
        self.assertEqual(result.processed, 1)

    def test_explicit_never_draft_feedback_suppresses_same_sender_and_category(self):
        first_graph = FakeGraph([EMAIL])
        EmailManager(self.settings, first_graph, FakeAssistant(), self.store).run()
        self.store.record_feedback(EMAIL.id, "never_draft_like_this", "Receipts do not need replies")

        similar = Email(
            id="message-2", subject="Another proposal", sender_name="Client", sender_email="client@example.com",
            received_at="2026-08-11T12:00:00Z", body_preview="Please review.", body="Please review.",
        )
        second_graph = FakeGraph([similar])
        result = EmailManager(self.settings, second_graph, FakeAssistant(), self.store).run()

        preference = self.store.reply_preference("client@example.com", "action")
        self.assertTrue(preference.suppress_drafts)
        self.assertEqual(self.store.list_reply_preferences(), [preference])
        self.assertEqual((result.processed, result.drafts_created), (1, 0))
        self.assertEqual(second_graph.drafts, [])

    def test_feedback_is_editable_and_is_passed_to_the_assessor(self):
        EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(), self.store).run()
        self.store.record_feedback(EMAIL.id, "draft_deleted", "Too eager")
        self.store.record_feedback(EMAIL.id, "draft_edited", "Actually useful after editing")
        records = self.store.list_feedback()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].feedback_type, "draft_edited")

        captured_profiles = []

        class CapturingAssistant(FakeAssistant):
            def assess(self, email, profile, draft_tone):
                captured_profiles.append(profile)
                return self.assessment

        new_email = Email(
            id="message-3", subject="Follow-up", sender_name="Client", sender_email="client@example.com",
            received_at="2026-08-11T12:00:00Z", body_preview="Please reply.", body="Please reply.",
        )
        EmailManager(self.settings, FakeGraph([new_email]), CapturingAssistant(), self.store).run()
        self.assertIn("Explicit local feedback", captured_profiles[0].response_preferences)
        self.assertTrue(self.store.remove_feedback(EMAIL.id))
        self.assertEqual(self.store.list_feedback(), [])

    def test_review_queue_includes_saved_message_details_and_escapes_content(self):
        unsafe = Email(
            id="message-4", subject="<unsafe>", sender_name="Client", sender_email="client@example.com",
            received_at="2026-08-11T12:00:00Z", body_preview="", body="", web_link="https://outlook.example/message-4",
        )
        EmailManager(self.settings, FakeGraph([unsafe]), FakeAssistant(), self.store).run()
        messages = self.store.list_processed_messages()
        self.assertEqual(messages[0].source_web_link, unsafe.web_link)
        page = render_dashboard(messages, self.store.list_reply_preferences())
        self.assertIn("Open email", page)
        self.assertIn("&lt;unsafe&gt;", page)
        self.assertNotIn("<h2><unsafe>", page)

    def test_dashboard_filters_and_shows_draft_link_and_reason(self):
        graph = FakeGraph([EMAIL])
        EmailManager(self.settings, graph, FakeAssistant(), self.store).run()
        cards = self.store.list_processed_messages(status="drafted", search="proposal")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].draft_web_link, "https://outlook.example/drafts/message-1")
        self.assertEqual(cards[0].draft_reason, "draft_created")
        page = render_dashboard(cards, self.store.list_reply_preferences(), self.store.list_runs(), {"status": "drafted"})
        self.assertIn("Open draft", page)
        self.assertIn('href="/activity"', page)

    def test_review_queue_shows_only_unreviewed_reply_decisions(self):
        EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(), self.store).run()
        queue = self.store.list_processed_messages(status="review")
        self.assertEqual(len(queue), 1)
        page = render_dashboard(queue, self.store.list_reply_preferences())
        self.assertIn("Was this draft useful?", page)
        self.assertIn("Avoid similar emails", page)

        self.store.record_feedback(EMAIL.id, "draft_sent")
        self.assertEqual(self.store.list_processed_messages(status="review"), [])

    def test_review_queue_excludes_legacy_records_without_dashboard_metadata(self):
        self.store.connection.execute(
            """INSERT INTO processed_messages
               (message_id, processed_at, category, needs_response, needs_action, draft_id, summary, sender_email, subject)
               VALUES (?, ?, ?, ?, ?, ?, ?, '', '')""",
            ("legacy", "2026-08-10T12:00:00Z", "action", True, True, "old-draft", "Legacy summary"),
        )
        self.store.connection.commit()
        self.assertEqual(self.store.list_processed_messages(status="review"), [])

    def test_control_center_pages_share_the_navigation(self):
        EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(), self.store).run()
        preferences = self.store.list_reply_preferences()
        runs = self.store.list_runs()
        pages = (
            render_activity(runs),
            render_preferences(preferences),
            render_folders(self.store.get_folder_profiles()),
        )
        for page in pages:
            self.assertIn("Email Manager", page)
            self.assertIn('href="/settings"', page)

    def test_settings_view_displays_safe_configuration_without_credentials(self):
        settings = SimpleNamespace(
            schedules=("06:00",), lookback_hours=24, digest_mode="draft", draft_tone="concise",
            folder_suggestions_enabled=True, folder_semantic_suggestions_enabled=False,
            no_reply_senders=("blocked@example.com",), no_reply_domains=("example.com",),
            database_path=Path("test.db"), openai_api_key="secret-value", client_id="secret-client",
        )
        page = render_settings(settings)
        self.assertIn("Active local configuration", page)
        self.assertNotIn("secret-value", page)
        self.assertNotIn("secret-client", page)

    def test_no_draft_reason_explains_model_decision(self):
        no_reply = Assessment(
            needs_response=False, needs_action=False, priority="low", category="informational",
            summary="For your information.", action_items=(), draft_reply=None,
            suggested_followup_time="none", confidence=0.9,
        )
        EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(no_reply), self.store).run()
        message = self.store.list_processed_messages()[0]
        self.assertEqual(message.draft_reason, "AI assessed that no personal reply is needed.")

    def test_refresh_dashboard_metadata_backfills_legacy_records_without_processing(self):
        self.store.connection.execute(
            """INSERT INTO processed_messages
               (message_id, processed_at, category, needs_response, needs_action, draft_id, summary, sender_email, subject, source_web_link)
               VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '')""",
            (EMAIL.id, "2026-08-10T12:00:00Z", "action", True, True, "draft-1", "Legacy summary"),
        )
        self.store.connection.commit()
        manager = EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(), self.store)

        self.assertEqual(manager.refresh_dashboard_metadata(), (1, 0))
        refreshed = self.store.list_processed_messages()[0]
        self.assertEqual((refreshed.sender_email, refreshed.subject, refreshed.source_web_link),
                         (EMAIL.sender_email, EMAIL.subject, EMAIL.web_link))

    def test_get_processed_message_returns_one_local_decision_without_graph_access(self):
        EmailManager(self.settings, FakeGraph([EMAIL]), FakeAssistant(), self.store).run()
        message = self.store.get_processed_message(EMAIL.id)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual((message.message_id, message.subject, message.sender_email),
                         (EMAIL.id, EMAIL.subject, EMAIL.sender_email))
        self.assertIsNone(self.store.get_processed_message("not-a-local-message"))

    def test_process_message_is_idempotent_and_persists_decision_card_fields(self):
        graph = FakeGraph([EMAIL])
        manager = EmailManager(self.settings, graph, FakeAssistant(), self.store)
        first = manager.process_message(EMAIL.id)
        second = manager.process_message(EMAIL.id)
        stored = self.store.get_processed_message(EMAIL.id)
        self.assertTrue(first[1])
        self.assertFalse(second[1])
        self.assertEqual(len(graph.drafts), 1)
        self.assertEqual((stored.priority, stored.action_items, stored.suggested_followup_time, stored.confidence),
                         ("high", ("Prepare proposal",), "today", 0.9))

    def test_work_style_override_and_conservative_policy_keep_hard_safety(self):
        self.store.save_work_style(WorkStyleProfile("warm", "brief", "Hello", "Thanks", "Me", "conservative"))
        low_confidence = Assessment(True, True, "medium", "action", "Reply requested", (), "I can help.", "today", 0.79)
        graph = FakeGraph([EMAIL])
        EmailManager(self.settings, graph, FakeAssistant(low_confidence), self.store).run()
        decision = self.store.get_processed_message(EMAIL.id)
        self.assertFalse(decision.has_draft)
        self.assertIn("conservative", decision.draft_reason)
        self.assertEqual(self.store.get_work_style().greeting, "Hello")


if __name__ == "__main__":
    unittest.main()
