"""Twice-daily, persistent notification digests. Never called by a search."""
from datetime import datetime, timedelta
import logging
import os
import smtplib
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from mailer import send_email, smtp_settings
import database


def due_slot(now: datetime | None = None) -> str | None:
    local = (now or datetime.now(ZoneInfo("America/Santiago"))).astimezone(ZoneInfo("America/Santiago"))
    for hour in (9, 16):
        scheduled = local.replace(hour=hour, minute=30, second=0, microsecond=0)
        if timedelta(0) <= local - scheduled < timedelta(hours=2):
            return f"{local.date().isoformat()}T{hour:02d}:30"
    return None


def match_identity(item: dict) -> tuple:
    return (item.get("actor_user_id", "").strip().lower(),
            item.get("card_name", "").strip().casefold(), item.get("kind"))


def claim_digest(user_id: str, slot: str) -> dict | None:
    now = time.time()
    with database.get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT state FROM email_digest_batches WHERE receiver_user_id = ? AND slot_key = ?", (user_id, slot)).fetchone()
        if previous and previous["state"] == "sent":
            return None
        busy = db.execute("SELECT 1 FROM email_digest_batches WHERE receiver_user_id = ? AND state = 'sending' AND claimed_at > ?", (user_id, now - 1800)).fetchone()
        if busy:
            return None
        for table in ("card_notifications", "messages"):
            db.execute(f"UPDATE {table} SET email_state = 'pending', email_batch_id = NULL WHERE email_batch_id IN "
                       "(SELECT batch_id FROM email_digest_batches WHERE receiver_user_id = ? AND state = 'sending' AND claimed_at <= ?)", (user_id, now - 1800))
        db.execute("UPDATE email_digest_batches SET state = 'failed' WHERE receiver_user_id = ? AND state = 'sending' AND claimed_at <= ?", (user_id, now - 1800))
        # Recover a stale delivery claim made by the previous immediate-mail version.
        db.execute("UPDATE card_notifications SET email_state = 'pending' WHERE receiver_user_id = ? "
                   "AND email_state = 'sending' AND email_batch_id IS NULL AND COALESCE(email_claimed_at, 0) <= ?", (user_id, now - 1800))
        rows = db.execute("SELECT * FROM card_notifications WHERE receiver_user_id = ? ORDER BY id", (user_id,)).fetchall()
        seen = {match_identity(dict(row)) for row in rows if row["email_state"] == "sent"}
        matches = []
        for row in rows:
            if row["email_state"] not in (None, "pending"):
                continue
            item = dict(row)
            identity = match_identity(item)
            if identity in seen:
                db.execute("UPDATE card_notifications SET email_state = 'duplicate' WHERE id = ?", (row["id"],))
                continue
            seen.add(identity)
            matches.append(item)
        # Old messages without an explicit queued state are not mailed retroactively.
        messages = [dict(row) for row in db.execute("SELECT * FROM messages WHERE receiver_user_id = ? AND email_state = 'pending' AND read = 0 ORDER BY id", (user_id,)).fetchall()]
        if not matches and not messages:
            return None
        batch_id = uuid4().hex
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM email_digest_batches").fetchone()[0]
        db.execute("INSERT INTO email_digest_batches (position, batch_id, receiver_user_id, slot_key, state, claimed_at) "
                   "VALUES (?, ?, ?, ?, 'sending', ?) ON CONFLICT(receiver_user_id, slot_key) DO UPDATE SET "
                   "batch_id = excluded.batch_id, state = 'sending', claimed_at = excluded.claimed_at", (position, batch_id, user_id, slot, now))
        for table, items in (("card_notifications", matches), ("messages", messages)):
            db.executemany(f"UPDATE {table} SET email_state = 'sending', email_batch_id = ? WHERE id = ?", [(batch_id, item["id"]) for item in items])
        return {"batch_id": batch_id, "matches": matches, "messages": messages}


def finish_digest(batch_id: str, sent: bool) -> None:
    with database.get_connection() as db:
        for table in ("card_notifications", "messages"):
            db.execute(f"UPDATE {table} SET email_state = ? WHERE email_batch_id = ? AND email_state = 'sending'", ("sent" if sent else "pending", batch_id))
        db.execute("UPDATE email_digest_batches SET state = ?, sent_at = ? WHERE batch_id = ?", ("sent" if sent else "failed", datetime.now(ZoneInfo("America/Santiago")).isoformat() if sent else None, batch_id))


def digest_body(user: dict, batch: dict) -> str:
    base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    lines = [f"Hola {user.get('name') or 'usuario'},", "", "Estas son tus nuevas notificaciones de Findmycard:", ""]
    if batch["matches"]:
        lines.append("Coincidencias de cartas:")
        for item in batch["matches"]:
            action = "busca una carta que ofreces" if item["kind"] == "searched" else "ofrece una carta que buscas"
            lines.append(f"- {item['card_name']}: {item.get('actor_name') or 'Un usuario'} {action}.")
        lines += [f"{base_url}/notifications", ""]
    if batch["messages"]:
        senders = sorted({item.get("sender_name") or "Un usuario" for item in batch["messages"]})
        lines += [f"Tienes {len(batch['messages'])} mensaje(s) nuevo(s) de: {', '.join(senders)}.", f"{base_url}/messages", ""]
    lines.append("Inicia sesion para revisar tus avisos y contactar a los usuarios.")
    return "\n".join(lines)


def send_due_digests(now: datetime | None = None) -> dict:
    slot = due_slot(now)
    report = {"slot": slot, "sent": 0, "failed": 0}
    if slot is None:
        return report
    if smtp_settings() is None:
        raise ValueError("Falta configurar SMTP.")
    with database.get_connection() as db:
        recipients = {row[0] for row in db.execute("SELECT receiver_user_id FROM card_notifications WHERE email_state IS NULL OR email_state IN ('pending', 'sending') "
                    "UNION SELECT receiver_user_id FROM messages WHERE email_state IN ('pending', 'sending') AND read = 0").fetchall()}
    users = {user.get("user_id"): user for user in database.load_collection("users")}
    for user_id in sorted(value for value in recipients if value):
        user = users.get(user_id)
        if not user or not user.get("email"):
            continue
        batch = claim_digest(user_id, slot)
        if not batch:
            continue
        sent = False
        try:
            sent = send_email(user["email"], "Tus nuevas notificaciones de Findmycard", digest_body(user, batch))
        except (OSError, smtplib.SMTPException, ValueError):
            logging.getLogger(__name__).exception("No se pudo enviar un resumen de notificaciones.")
        finally:
            finish_digest(batch["batch_id"], sent)
        report["sent" if sent else "failed"] += 1
    return report
