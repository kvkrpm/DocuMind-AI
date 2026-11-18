from contextlib import ExitStack
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.email_service import send_email


class EmailServiceTests(unittest.TestCase):
    def test_sends_authenticated_email_over_starttls(self):
        patches = [
            patch.object(settings, "SMTP_HOST", "smtp.example.com"),
            patch.object(settings, "SMTP_PORT", 587),
            patch.object(settings, "SMTP_USERNAME", "mailer"),
            patch.object(settings, "SMTP_PASSWORD", "test-password"),
            patch.object(settings, "SMTP_FROM_EMAIL", "noreply@example.com"),
            patch.object(settings, "SMTP_USE_SSL", False),
            patch.object(settings, "SMTP_STARTTLS", True),
            patch("app.services.email_service.smtplib.SMTP"),
        ]
        with ExitStack() as stack:
            for patcher in patches[:-1]:
                stack.enter_context(patcher)
            smtp = stack.enter_context(patches[-1])
            server = smtp.return_value.__enter__.return_value

            send_email("user@example.com", "Verify", "Use this link.")

        smtp.assert_called_once_with("smtp.example.com", 587, timeout=15)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("mailer", "test-password")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "user@example.com")
        self.assertEqual(message["Subject"], "Verify")
        self.assertIn("Use this link.", message.get_content())


if __name__ == "__main__":
    unittest.main()
