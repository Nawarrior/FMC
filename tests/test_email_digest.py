from datetime import datetime
import os
import smtplib
import sqlite3
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import database
import email_digest

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        connection = patch.object(database, "get_connection", return_value=self.db)
        connection.start()
        self.addCleanup(connection.stop)
        database.initialize_database()
        database.save_collection("users", [{"user_id": "receiver", "name": "Receiver", "email": "receiver@example.test"}])
        self.env = patch.dict(os.environ, {"SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "sender@example.test", "SMTP_PASSWORD": "test", "APP_BASE_URL": "https://example.test"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.send_patch = patch.object(email_digest, "send_email", return_value=True)
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.morning = datetime(2026, 10, 15, 9, 30, tzinfo=ZoneInfo("America/Santiago"))
        self.evening = self.morning.replace(hour=16)

    def notice(self, key, name="Duress", state=None):
        database.add_card_notifications([{"notification_id": key, "dedup_key": key, "receiver_user_id": "receiver", "actor_user_id": "actor", "actor_name": "Actor", "card_name": name, "kind": "searched", "email_state": state, "read": False}])

    def test_groups_cards_and_messages_in_one_email_and_does_not_repeat(self):
        self.notice("one")
        self.notice("two", "Thoughtseize")
        database.save_collection("messages", [{"message_id": "message", "sender_name": "Sender", "receiver_user_id": "receiver", "text": "Hello", "email_state": "pending", "read": False}])
        report = email_digest.send_due_digests(self.morning)
        self.assertEqual(report["sent"], 1)
        self.send.assert_called_once()
        body = self.send.call_args.args[2]
        self.assertIn("Duress", body)
        self.assertIn("Thoughtseize", body)
        self.assertIn("1 mensaje", body)
        self.assertEqual(email_digest.send_due_digests(self.morning)["sent"], 0)
        self.assertEqual(email_digest.send_due_digests(self.evening)["sent"], 0)

    def test_new_items_wait_for_next_slot_after_user_has_received_summary(self):
        self.notice("one")
        email_digest.send_due_digests(self.morning)
        self.notice("two", "Thoughtseize")
        self.assertEqual(email_digest.send_due_digests(self.morning)["sent"], 0)
        self.assertEqual(email_digest.send_due_digests(self.evening)["sent"], 1)
        self.assertEqual(self.send.call_count, 2)
        self.assertNotIn("Duress", self.send.call_args.args[2])

    def test_daily_duplicate_of_previously_emailed_match_is_never_sent_again(self):
        self.notice("yesterday", state="sent")
        self.notice("today")
        self.assertEqual(email_digest.send_due_digests(self.morning)["sent"], 0)
        self.send.assert_not_called()
        self.assertEqual(database.load_card_notifications("receiver")[0]["email_state"], "duplicate")

    def test_smtp_failure_preserves_pending_items_for_retry(self):
        self.notice("one")
        self.send.side_effect = smtplib.SMTPException("offline")
        with patch.object(email_digest.logging.getLogger(email_digest.__name__), "exception"):
            self.assertEqual(email_digest.send_due_digests(self.morning)["failed"], 1)
        self.assertEqual(database.load_card_notifications("receiver")[0]["email_state"], "pending")
        self.send.side_effect = None
        self.assertEqual(email_digest.send_due_digests(self.morning)["sent"], 1)

    def test_parallel_run_cannot_claim_same_recipient(self):
        self.notice("one")
        batch = email_digest.claim_digest("receiver", "2026-10-15T09:30")
        self.assertIsNotNone(batch)
        self.assertIsNone(email_digest.claim_digest("receiver", "2026-10-15T09:30"))
        self.assertIsNone(email_digest.claim_digest("receiver", "2026-10-15T16:30"))

    def test_local_times_follow_chilean_daylight_saving_and_skip_other_hours(self):
        # UTC-3 in October; UTC-4 in July.
        self.assertEqual(email_digest.due_slot(datetime.fromisoformat("2026-10-15T12:30:00+00:00")), "2026-10-15T09:30")
        self.assertEqual(email_digest.due_slot(datetime.fromisoformat("2026-07-15T13:30:00+00:00")), "2026-07-15T09:30")
        self.assertIsNone(email_digest.due_slot(self.morning.replace(hour=8)))
        self.assertIsNone(email_digest.due_slot(self.morning.replace(hour=13)))
        self.assertEqual(email_digest.send_due_digests(self.morning.replace(hour=13))["sent"], 0)
        self.send.assert_not_called()

    def test_read_and_legacy_messages_are_not_mailed(self):
        database.save_collection("messages", [
            {"message_id": "old", "receiver_user_id": "receiver", "read": False},
            {"message_id": "seen", "receiver_user_id": "receiver", "email_state": "pending", "read": True},
        ])
        self.assertEqual(email_digest.send_due_digests(self.morning)["sent"], 0)
        self.send.assert_not_called()

    def test_scheduler_endpoint_requires_private_token_and_normal_posts_keep_csrf(self):
        client = application.app.test_client()
        with patch.dict(os.environ, {"DIGEST_SCHEDULER_TOKEN": "private-test-token"}), patch.object(email_digest, "send_due_digests", return_value={"slot": None, "sent": 0, "failed": 0}) as run:
            self.assertEqual(client.post("/internal/email-digest").status_code, 403)
            self.assertEqual(client.post("/internal/email-digest", headers={"Authorization": "Bearer wrong"}).status_code, 403)
            self.assertEqual(client.post("/internal/email-digest", headers={"Authorization": "Bearer private-test-token"}).status_code, 200)
            run.assert_called_once()
            self.assertEqual(client.post("/messages/send").status_code, 400)


if __name__ == "__main__":
    unittest.main()
