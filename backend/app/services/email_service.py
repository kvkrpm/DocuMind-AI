import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings


def is_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def send_email(recipient: str, subject: str, body: str) -> None:
    if not is_configured():
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL must be configured")

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=15,
            context=context,
        ) as server:
            _send_message(server, message)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_STARTTLS:
                server.starttls(context=context)
            _send_message(server, message)


def _send_message(server: smtplib.SMTP, message: EmailMessage) -> None:
    if settings.SMTP_USERNAME:
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
    server.send_message(message)
