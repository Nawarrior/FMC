import os
import smtplib
import unittest
from unittest.mock import MagicMock, patch

import mailer

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.test", "SMTP_PASSWORD": "test-password",
            "APP_BASE_URL": "https://nawarrior.pythonanywhere.com",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def connection(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}
        return smtp

    def test_starttls_authentication_and_utf8_message(self):
        smtp = self.connection()
        with patch.object(mailer.smtplib, "SMTP", return_value=smtp) as transport:
            self.assertTrue(mailer.send_email("recipient@example.test", "Coincidencia: Duress", "También te podría interesar"))
        transport.assert_called_once_with("smtp.example.test", 587, timeout=15)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("sender@example.test", "test-password")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "recipient@example.test")
        self.assertIn("También", message.get_content())

    def test_ssl_465_does_not_attempt_starttls(self):
        os.environ.update(SMTP_PORT="465", SMTP_USE_SSL="true")
        smtp = self.connection()
        with patch.object(mailer.smtplib, "SMTP_SSL", return_value=smtp):
            self.assertTrue(mailer.send_email("recipient@example.test", "Test", "Body"))
        smtp.starttls.assert_not_called()

    def test_missing_config_and_incomplete_credentials_do_not_send(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(mailer.smtplib, "SMTP") as transport:
            self.assertFalse(mailer.send_email("recipient@example.test", "Test", "Body"))
            transport.assert_not_called()
        os.environ["SMTP_PASSWORD"] = ""
        with self.assertRaises(ValueError):
            mailer.smtp_settings()

    def test_rejected_recipient_is_not_reported_as_sent(self):
        smtp = self.connection()
        smtp.send_message.return_value = {"recipient@example.test": (550, b"rejected")}
        with patch.object(mailer.smtplib, "SMTP", return_value=smtp), self.assertRaises(smtplib.SMTPRecipientsRefused):
            mailer.send_email("recipient@example.test", "Test", "Body")

    def test_all_notification_types_use_same_transport(self):
        receiver = {"name": "Receiver", "email": "receiver@example.test"}
        sender = {"name": "Sender"}
        with application.app.test_request_context(), patch.object(application, "send_email", return_value=True) as send:
            self.assertTrue(application.send_message_notification(receiver, sender, "Duress"))
            self.assertTrue(application.send_password_reset_email(receiver, "https://example.test/reset/token"))
            self.assertTrue(application.notify_card_match_recipient(receiver, sender, "Duress", "searched"))
            self.assertTrue(application.notify_card_match_recipient(receiver, sender, "Duress", "offered"))
        self.assertEqual(send.call_count, 4)
        self.assertIn("busca una carta", send.call_args_list[2].args[2])
        self.assertIn("ofrece una carta", send.call_args_list[3].args[2])
        self.assertIn("https://nawarrior.pythonanywhere.com/notifications", send.call_args_list[2].args[2])

    def test_smtp_failure_preserves_user_flow(self):
        with application.app.test_request_context(), \
             patch.object(application, "send_email", side_effect=smtplib.SMTPAuthenticationError(535, b"denied")), \
             patch.object(application.app.logger, "exception") as log:
            self.assertFalse(application.notify_card_match_recipient({"email": "recipient@example.test"}, {}, "Duress", "offered"))
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
