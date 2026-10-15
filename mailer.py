"""Shared SMTP transport for messages, password recovery and card matches."""
from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import ssl

from dotenv import load_dotenv


# WSGI workers load this file too; Bash exports alone do not configure them.
load_dotenv(Path(__file__).with_name(".env"))


def smtp_settings() -> dict | None:
    host = os.environ.get("SMTP_HOST", "").strip()
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM_EMAIL", "").strip() or username
    if not host or not sender:
        return None
    if bool(username) != bool(password):
        raise ValueError("SMTP_USERNAME y SMTP_PASSWORD deben configurarse juntos.")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError as exc:
        raise ValueError("SMTP_PORT debe ser un numero valido.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT debe estar entre 1 y 65535.")
    use_ssl = os.environ.get("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes"}
    use_starttls = os.environ.get("SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    return {"host": host, "port": port, "username": username, "password": password,
            "sender": sender, "use_ssl": use_ssl, "use_starttls": use_starttls}


def open_smtp(settings: dict):
    if settings["use_ssl"]:
        connection = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=15, context=ssl.create_default_context())
    else:
        connection = smtplib.SMTP(settings["host"], settings["port"], timeout=15)
    try:
        if not settings["use_ssl"] and settings["use_starttls"]:
            connection.starttls(context=ssl.create_default_context())
        if settings["username"]:
            connection.login(settings["username"], settings["password"])
        return connection
    except Exception:
        connection.close()
        raise


def send_email(receiver_email: str, subject: str, body: str) -> bool:
    settings = smtp_settings()
    if settings is None or not receiver_email.strip():
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["sender"]
    message["To"] = receiver_email.strip()
    message.set_content(body)
    with open_smtp(settings) as smtp:
        # Some SMTP servers report rejection through this return value.
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    return True
