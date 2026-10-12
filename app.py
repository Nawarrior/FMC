from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import ssl
from typing import Any
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import json
import time

from authlib.integrations.flask_client import OAuth
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import initialize_database, load_collection, migrate_json_files_to_db, save_collection


SCRYFALL_API = "https://api.scryfall.com"
REST_COUNTRIES_API = "https://restcountries.com/v3.1/all?fields=name,cca2,flags"
USER_AGENT = "FMC-MTG-Search/1.0"
SAVED_CARDS_FILE = Path(__file__).with_name("saved_cards.json")
USERS_FILE = Path(__file__).with_name("users.json")
SEARCH_LOG_FILE = Path(__file__).with_name("busquedas.json")
PRODUCTS_FILE = Path(__file__).with_name("products.json")
CART_FILE = Path(__file__).with_name("cart.json")
USER_REFERENCES_FILE = Path(__file__).with_name("referenciasUsr.json")
STORES_FILE = Path(__file__).with_name("shops.json")
STORE_REFERENCES_FILE = Path(__file__).with_name("referenciasTienda.json")
MESSAGES_FILE = Path(__file__).with_name("mensajes.json")
JSON_COLLECTIONS = {
    "saved_cards": SAVED_CARDS_FILE,
    "search_log": SEARCH_LOG_FILE,
    "products": PRODUCTS_FILE,
    "cart": CART_FILE,
    "user_references": USER_REFERENCES_FILE,
    "stores": STORES_FILE,
    "store_references": STORE_REFERENCES_FILE,
    "messages": MESSAGES_FILE,
    "users": USERS_FILE,
}
ROLE_OPTIONS = ("adm", "moderador", "usuario", "tienda")
COUNTRY_OPTIONS = (
    {"code": "CL", "label": "Chile", "flag": "🇨🇱"},
    {"code": "AR", "label": "Argentina", "flag": "🇦🇷"},
    {"code": "BR", "label": "Brasil", "flag": "🇧🇷"},
    {"code": "MX", "label": "Mexico", "flag": "🇲🇽"},
    {"code": "PE", "label": "Peru", "flag": "🇵🇪"},
    {"code": "UY", "label": "Uruguay", "flag": "🇺🇾"},
    {"code": "CO", "label": "Colombia", "flag": "🇨🇴"},
    {"code": "ES", "label": "Espana", "flag": "🇪🇸"},
)

COLOR_OPTIONS = (
    {"code": "W", "label": "Blanco"},
    {"code": "U", "label": "Azul"},
    {"code": "B", "label": "Negro"},
    {"code": "R", "label": "Rojo"},
    {"code": "G", "label": "Verde"},
    {"code": "C", "label": "Incoloro"},
)

LEGALITY_OPTIONS = (
    {"code": "legal", "label": "Legal"},
    {"code": "not_legal", "label": "No legal"},
    {"code": "restricted", "label": "Restringida"},
    {"code": "banned", "label": "Prohibida"},
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
app.config["GOOGLE_DISCOVERY_URL"] = "https://accounts.google.com/.well-known/openid-configuration"

oauth = OAuth(app)
google = oauth.register(
    name="google",
    server_metadata_url=app.config["GOOGLE_DISCOVERY_URL"],
    client_id=app.config["GOOGLE_CLIENT_ID"] or None,
    client_secret=app.config["GOOGLE_CLIENT_SECRET"] or None,
    client_kwargs={"scope": "openid email profile"},
)

initialize_database()
migrate_json_files_to_db(JSON_COLLECTIONS)


@dataclass
class SearchState:
    query: str = ""
    results: list[dict[str, Any]] | None = None
    error: str | None = None


def google_auth_ready() -> bool:
    return bool(app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"])


@app.context_processor
def inject_globals() -> dict[str, Any]:
    if session.get("user", {}).get("email"):
        sync_session_user(session["user"].get("email"))
    return {
        "current_user": session.get("user"),
        "google_auth_ready": google_auth_ready(),
        "is_admin_user": is_admin_user(),
        "cart_count": len(get_current_cart_items()),
        "unread_message_count": get_unread_message_count(),
    }


def fetch_json(endpoint: str) -> dict[str, Any]:
    return fetch_json_from_url(f"{SCRYFALL_API}{endpoint}")


def fetch_json_from_url(url: str) -> dict[str, Any]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    for attempt in range(3):
        try:
            with urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                details = payload.get("details") or payload.get("object") or "Error en Scryfall."
            except Exception:
                details = "Error en Scryfall."
            raise RuntimeError(details) from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                reason = getattr(exc, "reason", exc)
                raise RuntimeError(f"No se pudo conectar con Scryfall: {reason}") from exc
            time.sleep(0.5 * (2 ** attempt))


def fetch_all_pages(url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_url = url

    while next_url:
        payload = fetch_json_from_url(next_url)
        items.extend(payload.get("data", []))
        next_url = payload.get("next_page") if payload.get("has_more") else None

    return items


def search_cards(query: str) -> list[dict[str, Any]]:
    payload = fetch_json(f"/cards/search?q={quote(query)}&order=released")
    return payload.get("data", [])


def get_card(card_id: str) -> dict[str, Any]:
    return fetch_json(f"/cards/{quote(card_id)}")


def autocomplete_cards(query: str) -> list[str]:
    payload = fetch_json(f"/cards/autocomplete?q={quote(query)}")
    return payload.get("data", [])


def first_available_image(card: dict[str, Any]) -> str | None:
    image_uris = card.get("image_uris")
    if image_uris:
        return image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")

    for face in card.get("card_faces", []):
        face_uris = face.get("image_uris")
        if face_uris:
            return face_uris.get("normal") or face_uris.get("large") or face_uris.get("small")

    return None


def summarize_card(card: dict[str, Any]) -> dict[str, Any]:
    faces = card.get("card_faces") or []
    first_face = faces[0] if faces else {}

    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "set_name": card.get("set_name"),
        "released_at": card.get("released_at"),
        "mana_cost": card.get("mana_cost") or first_face.get("mana_cost"),
        "type_line": card.get("type_line") or first_face.get("type_line"),
        "oracle_text": card.get("oracle_text") or first_face.get("oracle_text"),
        "power": card.get("power") or first_face.get("power"),
        "toughness": card.get("toughness") or first_face.get("toughness"),
        "colors": card.get("colors") or first_face.get("colors") or [],
        "rarity": card.get("rarity"),
        "collector_number": card.get("collector_number"),
        "image_url": first_available_image(card),
    }


def build_card_detail(card: dict[str, Any]) -> dict[str, Any]:
    purchase = card.get("purchase_uris", {})
    prices = card.get("prices", {})

    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "mana_cost": card.get("mana_cost"),
        "cmc": card.get("cmc"),
        "type_line": card.get("type_line"),
        "oracle_text": card.get("oracle_text"),
        "flavor_text": card.get("flavor_text"),
        "colors": card.get("colors", []),
        "keywords": card.get("keywords", []),
        "rarity": card.get("rarity"),
        "set_name": card.get("set_name"),
        "set_code": card.get("set"),
        "collector_number": card.get("collector_number"),
        "released_at": card.get("released_at"),
        "artist": card.get("artist"),
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "loyalty": card.get("loyalty"),
        "defense": card.get("defense"),
        "image_url": first_available_image(card),
        "scryfall_uri": card.get("scryfall_uri"),
        "usd_price": prices.get("usd"),
        "usd_foil_price": prices.get("usd_foil"),
        "eur_price": prices.get("eur"),
        "tcgplayer": purchase.get("tcgplayer"),
        "cardmarket": purchase.get("cardmarket"),
    }


def build_edition_option(card: dict[str, Any], selected_id: str) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "label": card.get("set_name") or "Set desconocido",
        "set_code": (card.get("set") or "").upper(),
        "collector_number": card.get("collector_number") or "N/D",
        "released_at": card.get("released_at") or "N/D",
        "selected": card.get("id") == selected_id,
    }


def get_card_editions(card: dict[str, Any], selected_id: str) -> list[dict[str, Any]]:
    prints_search_uri = card.get("prints_search_uri")
    if not prints_search_uri:
        return []

    editions = [build_edition_option(printing, selected_id) for printing in fetch_all_pages(prints_search_uri)]
    editions.sort(
        key=lambda edition: (
            edition["released_at"] != "N/D",
            edition["released_at"],
        ),
        reverse=True,
    )
    return editions


def load_saved_cards() -> list[dict[str, Any]]:
    return load_collection("saved_cards")


def save_saved_cards(items: list[dict[str, Any]]) -> None:
    save_collection("saved_cards", items)


def load_search_log() -> list[dict[str, Any]]:
    return load_collection("search_log")


def save_search_log(items: list[dict[str, Any]]) -> None:
    save_collection("search_log", items)


def append_search_log(search_term: str, card_metadata: dict[str, Any] | None = None) -> None:
    normalized_term = search_term.strip()
    if not normalized_term:
        return

    entry = {
        "search_term": normalized_term,
        "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if card_metadata:
        entry.update(card_metadata)

    history = load_search_log()
    history.append(entry)
    save_search_log(history)


def load_products() -> list[dict[str, Any]]:
    return load_collection("products")


def save_products(items: list[dict[str, Any]]) -> None:
    save_collection("products", items)


def load_cart() -> list[dict[str, Any]]:
    return load_collection("cart")


def save_cart(items: list[dict[str, Any]]) -> None:
    save_collection("cart", items)


def load_user_references() -> list[dict[str, Any]]:
    return load_collection("user_references")


def save_user_references(items: list[dict[str, Any]]) -> None:
    save_collection("user_references", items)


def load_stores() -> list[dict[str, Any]]:
    return load_collection("stores")


def save_stores(items: list[dict[str, Any]]) -> None:
    save_collection("stores", items)


def load_store_references() -> list[dict[str, Any]]:
    return load_collection("store_references")


def save_store_references(items: list[dict[str, Any]]) -> None:
    save_collection("store_references", items)


def load_messages() -> list[dict[str, Any]]:
    return load_collection("messages")


def save_messages(items: list[dict[str, Any]]) -> None:
    save_collection("messages", items)


def normalize_price(value: str) -> float:
    sanitized = value.replace(",", ".").strip()
    try:
        return max(0.0, round(float(sanitized), 2))
    except ValueError:
        return 0.0


def normalize_quantity(value: str) -> int:
    try:
        return max(0, int(value.strip()))
    except ValueError:
        return 0


def load_users() -> list[dict[str, Any]]:
    return load_collection("users")


def save_users(items: list[dict[str, Any]]) -> None:
    save_collection("users", items)


def normalize_role(role: str | None) -> str:
    normalized_role = (role or "").strip().lower()
    return normalized_role if normalized_role in ROLE_OPTIONS else "usuario"


def normalize_country(country_code: str | None) -> str:
    normalized_code = (country_code or "").strip().upper()
    valid_codes = {item["code"] for item in COUNTRY_OPTIONS}
    return normalized_code if normalized_code in valid_codes else "CL"


def normalize_website(url: str | None) -> str:
    normalized = (url or "").strip()
    if not normalized:
        return ""
    if normalized.startswith(("http://", "https://")):
        return normalized
    return f"https://{normalized}"


def get_country_options() -> list[dict[str, str]]:
    try:
        payload = fetch_json_from_url(REST_COUNTRIES_API)
        countries = []
        for item in payload:
            countries.append(
                {
                    "code": (item.get("cca2") or "").upper(),
                    "label": ((item.get("name") or {}).get("common") or "").strip(),
                    "flag": ((item.get("flags") or {}).get("png") or "").strip(),
                }
            )

        countries = [item for item in countries if item["code"] and item["label"]]
        countries.sort(key=lambda item: item["label"])

        chile = next((item for item in countries if item["code"] == "CL"), None)
        if chile:
            countries = [chile] + [item for item in countries if item["code"] != "CL"]
        return countries
    except Exception:
        return [
            {
                "code": item["code"],
                "label": item["label"],
                "flag": "",
            }
            for item in COUNTRY_OPTIONS
        ]


def ensure_user_roles(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_users: list[dict[str, Any]] = []
    has_admin = any(normalize_role(item.get("role")) == "adm" for item in users)

    for index, item in enumerate(users):
        normalized = dict(item)
        if not (normalized.get("user_id") or "").strip():
            normalized["user_id"] = uuid4().hex[:12]
        role = normalize_role(normalized.get("role"))
        if not has_admin and index == 0:
            role = "adm"
        normalized["role"] = role
        normalized_users.append(normalized)

    return normalized_users


def find_user_by_email(email: str) -> dict[str, Any] | None:
    normalized_email = email.strip().lower()
    if not normalized_email:
        return None

    return next((item for item in get_users() if item.get("email", "").lower() == normalized_email), None)


def find_user_by_id(user_id: str) -> dict[str, Any] | None:
    normalized_user_id = (user_id or "").strip().lower()
    if not normalized_user_id:
        return None

    return next((item for item in get_users() if (item.get("user_id") or "").strip().lower() == normalized_user_id), None)


def get_users() -> list[dict[str, Any]]:
    users = load_users()
    normalized_users = ensure_user_roles(users)
    if normalized_users != users:
        save_users(normalized_users)
    return normalized_users


def find_user_index_by_email(email: str) -> int:
    normalized_email = email.strip().lower()
    users = get_users()
    for index, user in enumerate(users):
        if (user.get("email") or "").strip().lower() == normalized_email:
            return index
    return -1


def is_admin_user() -> bool:
    return normalize_role(session.get("user", {}).get("role")) == "adm"


def is_authenticated_user() -> bool:
    return bool(session.get("user", {}).get("email"))


def current_user_identity() -> dict[str, str]:
    user = session.get("user", {})
    return {
        "user_id": (user.get("user_id") or "").strip().lower(),
        "name": user.get("name") or "Usuario",
        "email": (user.get("email") or "").strip().lower(),
    }


def get_country_meta(country_code: str | None) -> dict[str, str]:
    normalized_code = normalize_country(country_code)
    for item in get_country_options():
        if item.get("code") == normalized_code:
            return item

    fallback = next((item for item in COUNTRY_OPTIONS if item["code"] == normalized_code), COUNTRY_OPTIONS[0])
    return {
        "code": fallback["code"],
        "label": fallback["label"],
        "flag": "",
    }


def get_current_cart_items() -> list[dict[str, Any]]:
    if not is_authenticated_user():
        return []

    current_email = current_user_identity().get("email", "")
    items = []
    for index, item in enumerate(load_cart()):
        if (item.get("user_email") or "").strip().lower() == current_email:
            items.append(
                {
                    **item,
                    "index": index,
                }
            )
    return items


def build_map_embed_url(address: str) -> str:
    normalized_address = (address or "").strip()
    if not normalized_address:
        return ""
    return f"https://www.google.com/maps?q={quote(normalized_address)}&output=embed"


def build_map_link(address: str) -> str:
    normalized_address = (address or "").strip()
    if not normalized_address:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote(normalized_address)}"


def get_unread_message_count() -> int:
    current_user_id = current_user_identity().get("user_id", "")
    if not current_user_id:
        return 0

    return sum(
        1
        for item in load_messages()
        if (item.get("receiver_user_id") or "").strip().lower() == current_user_id and not item.get("read", False)
    )


def build_message_threads() -> list[dict[str, Any]]:
    current_user = current_user_identity()
    current_user_id = current_user.get("user_id", "")
    if not current_user_id:
        return []

    grouped: dict[str, dict[str, Any]] = {}
    for message in load_messages():
        sender_id = (message.get("sender_user_id") or "").strip().lower()
        receiver_id = (message.get("receiver_user_id") or "").strip().lower()
        if current_user_id not in {sender_id, receiver_id}:
            continue

        other_id = receiver_id if sender_id == current_user_id else sender_id
        other_name = message.get("receiver_name") if sender_id == current_user_id else message.get("sender_name")
        thread = grouped.setdefault(
            other_id,
            {
                "other_user_id": other_id,
                "other_user_name": other_name or "Usuario",
                "other_profile_url": url_for("user_profile_page", user_id=other_id) if other_id else "",
                "messages": [],
            },
        )
        thread["messages"].append(message)

    ordered_threads = []
    for thread in grouped.values():
        thread["messages"].sort(key=lambda item: item.get("created_at", ""))
        thread["last_message_at"] = thread["messages"][-1].get("created_at", "") if thread["messages"] else ""
        ordered_threads.append(thread)

    ordered_threads.sort(key=lambda item: item.get("last_message_at", ""), reverse=True)
    return ordered_threads


def send_message_notification(receiver: dict[str, Any], sender: dict[str, str], card_name: str = "") -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    from_email = os.environ.get("SMTP_FROM_EMAIL", "").strip() or smtp_username
    receiver_email = (receiver.get("email") or "").strip()

    if not smtp_host or not from_email or not receiver_email:
        return False
    if bool(smtp_username) != bool(smtp_password):
        raise ValueError("SMTP_USERNAME y SMTP_PASSWORD deben configurarse juntos.")

    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError as exc:
        raise ValueError("SMTP_PORT debe ser un numero valido.") from exc

    use_ssl = os.environ.get("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes"}
    use_starttls = os.environ.get("SMTP_USE_STARTTLS", "true").strip().lower() in {"1", "true", "yes"}
    app_base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    messages_url = f"{app_base_url}{url_for('messages_page')}" if app_base_url else url_for("messages_page", _external=True)
    card_context = f" sobre {card_name}" if card_name else ""

    notification = EmailMessage()
    notification["Subject"] = "Nuevo mensaje en Findmycard"
    notification["From"] = from_email
    notification["To"] = receiver_email
    notification.set_content(
        f"Hola {receiver.get('name') or 'usuario'},\n\n"
        f"{sender.get('name') or 'Un usuario'} te envio un mensaje{card_context} en Findmycard.\n"
        "Inicia sesion para leerlo y responder.\n\n"
        f"{messages_url}\n"
    )

    if use_ssl:
        smtp_connection = smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=15,
            context=ssl.create_default_context(),
        )
    else:
        smtp_connection = smtplib.SMTP(smtp_host, smtp_port, timeout=15)

    with smtp_connection as smtp:
        if not use_ssl and use_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(notification)

    return True


def notify_message_recipient(receiver: dict[str, Any], sender: dict[str, str], card_name: str = "") -> bool:
    try:
        return send_message_notification(receiver, sender, card_name)
    except (OSError, smtplib.SMTPException, ValueError):
        app.logger.exception("No se pudo enviar la notificacion de mensaje por correo.")
        return False


def flash_message_delivery(notification_sent: bool) -> None:
    flash("Mensaje enviado.", "success")
    if notification_sent:
        flash("Notificacion por correo enviada al usuario receptor.", "success")
    else:
        flash("No se pudo enviar el correo de notificacion. Revisa la configuracion SMTP.", "error")


def mark_messages_as_read() -> None:
    current_user_id = current_user_identity().get("user_id", "")
    if not current_user_id:
        return

    messages = load_messages()
    changed = False
    for item in messages:
        if (item.get("receiver_user_id") or "").strip().lower() == current_user_id and not item.get("read", False):
            item["read"] = True
            changed = True

    if changed:
        save_messages(messages)


def can_manage_saved_item(item: dict[str, Any]) -> bool:
    if is_admin_user():
        return True

    owner_email = (item.get("user_email") or "").strip().lower()
    current_email = current_user_identity().get("email", "")
    if not current_email:
        return False

    return not owner_email or owner_email == current_email


def sync_session_user(email: str | None) -> None:
    if not email or "user" not in session:
        return

    stored_user = find_user_by_email(email)
    if not stored_user:
        return

    session["user"] = {
        **session["user"],
        "user_id": stored_user.get("user_id") or session["user"].get("user_id"),
        "name": stored_user.get("name") or session["user"].get("name"),
        "email": stored_user.get("email") or email,
        "provider": stored_user.get("provider") or session["user"].get("provider"),
        "role": normalize_role(stored_user.get("role")),
        "country": normalize_country(stored_user.get("country")),
    }


def find_saved_status(card_name: str, current_user_id: str = "") -> dict[str, Any] | None:
    normalized_name = (card_name or "").strip().lower()
    if not normalized_name:
        return None
    current_user_id = (current_user_id or "").strip().lower()

    matches = [
        item for item in load_saved_cards()
        if (item.get("name") or "").strip().lower() == normalized_name
        and not item.get("transaction_completed", False)
    ]
    if not matches:
        return None

    intents = []
    publications = []
    for item in matches:
        intent = (item.get("intent") or "").strip().lower()
        if intent and intent not in intents:
            intents.append(intent)

        owner_id = (item.get("user_id") or "").strip().lower()
        owner = find_user_by_id(owner_id) if owner_id else None
        if not owner and item.get("user_email"):
            owner = find_user_by_email((item.get("user_email") or "").strip().lower())
        owner_id = (owner or {}).get("user_id") or owner_id
        owner_name = (owner or {}).get("name") or item.get("user_name") or "Usuario"
        publications.append(
            {
                "intent": intent,
                "user_id": owner_id,
                "user_name": owner_name,
                "user_profile_url": url_for("user_profile_page", user_id=owner_id) if owner_id else "",
                "card_id": item.get("card_id") or "",
                "note": (item.get("note") or "").strip(),
                "is_current_user": bool(current_user_id and owner_id == current_user_id),
            }
        )

    return {
        "card_name": card_name,
        "intents": intents,
        "publications": publications,
        "current_user_has_publication": any(publication["is_current_user"] for publication in publications),
        "count": len(matches),
    }


def build_saved_card_view(item: dict[str, Any], index: int) -> dict[str, Any]:
    selected_id = item.get("card_id", "")
    editions: list[dict[str, Any]] = []
    image_url: str | None = None
    rarity = item.get("rarity", "")
    owner_user_id = (item.get("user_id") or "").strip().lower()

    if selected_id:
        try:
            selected_card_raw = get_card(selected_id)
            editions = get_card_editions(selected_card_raw, selected_id)
            image_url = first_available_image(selected_card_raw)
            rarity = selected_card_raw.get("rarity") or rarity
        except RuntimeError:
            editions = []

    if not owner_user_id and item.get("user_email"):
        owner = find_user_by_email((item.get("user_email") or "").strip().lower())
        owner_user_id = (owner or {}).get("user_id", "")

    return {
        **item,
        "index": index,
        "user_id": owner_user_id,
        "editions": editions,
        "image_url": image_url,
        "rarity": rarity,
        "can_manage": can_manage_saved_item(item),
        "user_profile_url": url_for("user_profile_page", user_id=owner_user_id) if owner_user_id else "",
    }


def build_search_history_view(item: dict[str, Any]) -> dict[str, Any]:
    image_url: str | None = item.get("image_url")
    if not image_url:
        search_term = item.get("search_term") or item.get("card_name") or ""
        if search_term:
            try:
                cards = search_cards(search_term)
                if cards:
                    image_url = first_available_image(cards[0])
            except RuntimeError:
                image_url = None

    return {
        **item,
        "image_url": image_url,
    }


def parse_search_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def build_search_dashboard_item(search_term: str, stats: dict[str, Any]) -> dict[str, Any]:
    image_url: str | None = None
    card_name = search_term
    colors: list[str] = []
    legalities: dict[str, str] = {}
    legality_summary: str = "N/D"

    card_name = stats.get("card_name") or search_term
    image_url = stats.get("image_url")
    colors = stats.get("colors") or []
    legalities = stats.get("legalities") or {}

    if not image_url and search_term:
        try:
            cards = search_cards(search_term)
            if cards:
                card = cards[0]
                image_url = first_available_image(card)
                card_name = card.get("name") or card_name
                colors = card.get("colors", []) or colors
                legalities = card.get("legalities") or legalities
        except RuntimeError:
            pass

    status_set = sorted({status for status in legalities.values() if status})
    legality_summary = ", ".join(status_set) if status_set else "N/D"

    return {
        "search_term": search_term,
        "card_name": card_name,
        "count": stats["count"],
        "first_searched_at": stats["first_searched_at"],
        "last_searched_at": stats["last_searched_at"],
        "image_url": image_url,
        "colors": colors,
        "legalities": legalities,
        "legality_summary": legality_summary,
    }


def filter_search_history_by_date(entries: list[dict[str, Any]], start_date: str | None, end_date: str | None) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    start_dt = parse_search_timestamp(f"{start_date} 00:00:00") if start_date else None
    end_dt = parse_search_timestamp(f"{end_date} 23:59:59") if end_date else None

    for item in entries:
        searched_at = parse_search_timestamp(item.get("searched_at"))
        if not searched_at:
            continue
        if start_dt and searched_at < start_dt:
            continue
        if end_dt and searched_at > end_dt:
            continue
        filtered.append(item)

    return filtered


def filter_dashboard_items(items: list[dict[str, Any]], color_filter: str, legality_filter: str) -> list[dict[str, Any]]:
    if not color_filter and not legality_filter:
        return items

    def matches_color(item: dict[str, Any]) -> bool:
        if not color_filter:
            return True
        if color_filter == "C":
            return len(item.get("colors", [])) == 0
        return color_filter in item.get("colors", [])

    def matches_legality(item: dict[str, Any]) -> bool:
        if not legality_filter:
            return True
        legalities = item.get("legalities", {})
        return any(status == legality_filter for status in legalities.values())

    return [item for item in items if matches_color(item) and matches_legality(item)]


def build_search_dashboard(entries: list[dict[str, Any]], color_filter: str, legality_filter: str) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}

    for item in entries:
        search_term = (item.get("search_term") or "").strip()
        if not search_term:
            continue

        stats = counts.setdefault(
            search_term,
            {
                "count": 0,
                "first_searched_at": item.get("searched_at"),
                "last_searched_at": item.get("searched_at"),
                "card_name": item.get("card_name"),
                "image_url": item.get("image_url"),
                "colors": item.get("colors") or [],
                "legalities": item.get("legalities") or {},
            },
        )
        if not stats.get("card_name") and item.get("card_name"):
            stats["card_name"] = item.get("card_name")
        if not stats.get("image_url") and item.get("image_url"):
            stats["image_url"] = item.get("image_url")
        if not stats.get("colors") and item.get("colors"):
            stats["colors"] = item.get("colors")
        if not stats.get("legalities") and item.get("legalities"):
            stats["legalities"] = item.get("legalities")
        stats["count"] += 1
        searched_at = item.get("searched_at") or ""
        if searched_at < stats["first_searched_at"]:
            stats["first_searched_at"] = searched_at
        if searched_at > stats["last_searched_at"]:
            stats["last_searched_at"] = searched_at

    dashboard_items = [build_search_dashboard_item(term, stats) for term, stats in counts.items()]
    dashboard_items = filter_dashboard_items(dashboard_items, color_filter, legality_filter)
    dashboard_items.sort(key=lambda entry: entry["count"], reverse=True)
    return dashboard_items[:10]


def build_user_reference_view(item: dict[str, Any]) -> dict[str, Any]:
    rating = max(1, min(5, int(item.get("rating") or 1)))
    author_user_id = (item.get("author_user_id") or "").strip().lower()
    if not author_user_id and item.get("author_email"):
        author = find_user_by_email((item.get("author_email") or "").strip().lower())
        author_user_id = (author or {}).get("user_id", "")
    return {
        **item,
        "rating": rating,
        "stars": "★" * rating + "☆" * (5 - rating),
        "author_profile_url": url_for("user_profile_page", user_id=author_user_id) if author_user_id else "",
    }


def find_store_by_id(shop_id: str) -> dict[str, Any] | None:
    normalized_shop_id = (shop_id or "").strip().lower()
    if not normalized_shop_id:
        return None

    return next((item for item in load_stores() if (item.get("shop_id") or "").strip().lower() == normalized_shop_id), None)


def build_store_reference_view(item: dict[str, Any]) -> dict[str, Any]:
    rating = max(1, min(5, int(item.get("rating") or 1)))
    author_user_id = (item.get("author_user_id") or "").strip().lower()
    return {
        **item,
        "rating": rating,
        "stars": "★" * rating + "☆" * (5 - rating),
        "author_profile_url": url_for("user_profile_page", user_id=author_user_id) if author_user_id else "",
    }


def build_store_view(item: dict[str, Any], index: int) -> dict[str, Any]:
    country = get_country_meta(item.get("country"))
    references = [
        reference
        for reference in load_store_references()
        if (reference.get("shop_id") or "").strip().lower() == (item.get("shop_id") or "").strip().lower()
    ]
    average_rating = round(sum(max(1, min(5, int(ref.get("rating") or 1))) for ref in references) / len(references), 1) if references else None
    website = normalize_website(item.get("website"))
    address = item.get("address", "").strip()

    return {
        **item,
        "index": index,
        "country_meta": country,
        "website": website,
        "map_embed_url": build_map_embed_url(address),
        "map_link": build_map_link(address),
        "average_rating": average_rating,
        "references_count": len(references),
        "detail_url": url_for("store_detail_page", shop_id=item.get("shop_id")),
    }


def build_store_profile_context(shop_id: str) -> dict[str, Any] | None:
    store = find_store_by_id(shop_id)
    if not store:
        return None

    views = [build_store_view(item, index) for index, item in enumerate(load_stores())]
    profile = next((item for item in views if (item.get("shop_id") or "").strip().lower() == (shop_id or "").strip().lower()), None)
    if not profile:
        return None

    references = [
        build_store_reference_view(item)
        for item in reversed(load_store_references())
        if (item.get("shop_id") or "").strip().lower() == (shop_id or "").strip().lower()
    ]
    profile["references"] = references
    profile["average_rating"] = round(sum(item["rating"] for item in references) / len(references), 1) if references else None
    return profile


def build_user_profile_context(user_id: str) -> dict[str, Any] | None:
    user = find_user_by_id(user_id)
    if not user:
        return None

    normalized_user_id = (user.get("user_id") or "").strip().lower()
    normalized_email = (user.get("email") or "").strip().lower()
    country = get_country_meta(user.get("country"))
    saved_cards = [
        build_saved_card_view(item, index)
        for index, item in enumerate(load_saved_cards())
        if (item.get("user_email") or "").strip().lower() == normalized_email
        and not item.get("transaction_completed", False)
    ]
    references = [
        build_user_reference_view(item)
        for item in reversed(load_user_references())
        if (
            (item.get("target_user_id") or "").strip().lower() == normalized_user_id
            or ((item.get("target_email") or "").strip().lower() == normalized_email)
        )
    ]
    average_rating = round(sum(item["rating"] for item in references) / len(references), 1) if references else None

    return {
        "user": user,
        "country": country,
        "saved_cards": saved_cards,
        "references": references,
        "average_rating": average_rating,
        "user_profile_url": url_for("user_profile_page", user_id=normalized_user_id),
    }


@app.route("/", methods=["GET"])
def index() -> str:
    query = request.args.get("q", "").strip()
    state = SearchState(query=query)

    if query:
        try:
            cards = search_cards(query)
            metadata: dict[str, Any] = {}
            if cards:
                first_card = cards[0]
                metadata = {
                    "card_name": first_card.get("name") or query,
                    "image_url": first_available_image(first_card),
                    "colors": first_card.get("colors", []) or [],
                    "legalities": first_card.get("legalities") or {},
                }
            append_search_log(query, metadata)
            if len(cards) == 1:
                return redirect(url_for("card_detail", card_id=cards[0].get("id")))
            state.results = [summarize_card(card) for card in cards]
        except RuntimeError as exc:
            append_search_log(query, {})
            state.error = str(exc)

    return render_template("home_page_v2.html", state=state, search_query=state.query)


@app.route("/news", methods=["GET"])
def news_page() -> str:
    return render_template("news_page.html", search_query="")


@app.route("/community-cards", methods=["GET"])
def community_cards_page() -> str:
    status_filter = request.args.get("status", "").strip().lower()
    rarity_filter = request.args.get("rarity", "").strip().lower()
    name_filter = request.args.get("name", "").strip()
    normalized_name_filter = name_filter.lower()
    saved_cards = [
        build_saved_card_view(item, index)
        for index, item in enumerate(load_saved_cards())
        if not item.get("transaction_completed", False)
    ]
    available_rarities = sorted({(item.get("rarity") or "").lower() for item in saved_cards if item.get("rarity")})

    if normalized_name_filter:
        saved_cards = [item for item in saved_cards if (item.get("name") or "").strip().lower() == normalized_name_filter]
    if status_filter:
        saved_cards = [item for item in saved_cards if (item.get("intent") or "").lower() == status_filter]
    if rarity_filter:
        saved_cards = [item for item in saved_cards if (item.get("rarity") or "").lower() == rarity_filter]

    return render_template(
        "community_cards_page.html",
        search_query="",
        saved_cards=saved_cards,
        status_filter=status_filter,
        rarity_filter=rarity_filter,
        name_filter=name_filter,
        available_rarities=available_rarities,
    )


@app.route("/register", methods=["GET"])
def register_page() -> str:
    return render_template("register_page.html", search_query="")


@app.route("/api/countries", methods=["GET"])
def countries_api() -> Any:
    return jsonify({"items": get_country_options()})


@app.route("/products", methods=["GET"])
def products_catalog_page() -> str:
    return render_template(
        "products_catalog_page.html",
        search_query="",
        products=load_products(),
    )


@app.route("/products/manage", methods=["GET"])
def products_page() -> Any:
    if not is_admin_user():
        flash("Solo el perfil adm puede acceder al mantenedor de productos.", "error")
        return redirect(url_for("products_catalog_page"))

    return render_template(
        "products_cards_page.html",
        search_query="",
        products=load_products(),
    )


@app.route("/stores", methods=["GET"])
def stores_catalog_page() -> str:
    stores = [build_store_view(item, index) for index, item in enumerate(load_stores())]
    return render_template(
        "stores_catalog_page.html",
        search_query="",
        stores=stores,
    )


@app.route("/stores/manage", methods=["GET"])
def stores_page() -> Any:
    if not is_admin_user():
        flash("Solo el perfil adm puede acceder al mantenedor de tiendas.", "error")
        return redirect(url_for("stores_catalog_page"))

    stores = [build_store_view(item, index) for index, item in enumerate(load_stores())]
    return render_template(
        "stores_page.html",
        search_query="",
        stores=stores,
        country_options=get_country_options(),
    )


@app.route("/stores/create", methods=["POST"])
def create_store() -> Any:
    if not is_admin_user():
        flash("No tienes permisos para crear tiendas.", "error")
        return redirect(url_for("stores_catalog_page"))

    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    if not name:
        flash("El nombre de la tienda es obligatorio.", "error")
        return redirect(url_for("stores_page"))
    if not address:
        flash("La direccion de la tienda es obligatoria.", "error")
        return redirect(url_for("stores_page"))

    stores = load_stores()
    stores.append(
        {
            "shop_id": uuid4().hex[:12],
            "name": name,
            "country": normalize_country(request.form.get("country")),
            "website": normalize_website(request.form.get("website")),
            "address": address,
        }
    )
    save_stores(stores)
    flash("Tienda creada.", "success")
    return redirect(url_for("stores_page"))


@app.route("/stores/<int:shop_index>/update", methods=["POST"])
def update_store(shop_index: int) -> Any:
    if not is_admin_user():
        flash("No tienes permisos para actualizar tiendas.", "error")
        return redirect(url_for("stores_catalog_page"))

    stores = load_stores()
    if shop_index < 0 or shop_index >= len(stores):
        flash("Tienda no encontrada.", "error")
        return redirect(url_for("stores_page"))

    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    if not name:
        flash("El nombre de la tienda es obligatorio.", "error")
        return redirect(url_for("stores_page"))
    if not address:
        flash("La direccion de la tienda es obligatoria.", "error")
        return redirect(url_for("stores_page"))

    stores[shop_index] = {
        "shop_id": stores[shop_index].get("shop_id") or uuid4().hex[:12],
        "name": name,
        "country": normalize_country(request.form.get("country")),
        "website": normalize_website(request.form.get("website")),
        "address": address,
    }
    save_stores(stores)
    flash("Tienda actualizada.", "success")
    return redirect(url_for("stores_page"))


@app.route("/stores/<int:shop_index>/delete", methods=["POST"])
def delete_store(shop_index: int) -> Any:
    if not is_admin_user():
        flash("No tienes permisos para eliminar tiendas.", "error")
        return redirect(url_for("stores_catalog_page"))

    stores = load_stores()
    if shop_index < 0 or shop_index >= len(stores):
        flash("Tienda no encontrada.", "error")
        return redirect(url_for("stores_page"))

    deleted_shop_id = (stores[shop_index].get("shop_id") or "").strip().lower()
    stores.pop(shop_index)
    save_stores(stores)

    references = [
        item for item in load_store_references()
        if (item.get("shop_id") or "").strip().lower() != deleted_shop_id
    ]
    save_store_references(references)
    flash("Tienda eliminada.", "success")
    return redirect(url_for("stores_page"))


@app.route("/stores/<shop_id>", methods=["GET"])
def store_detail_page(shop_id: str) -> Any:
    profile = build_store_profile_context(shop_id)
    if not profile:
        flash("Tienda no encontrada.", "error")
        return redirect(url_for("stores_catalog_page"))

    return render_template(
        "store_detail_page.html",
        search_query="",
        store=profile,
        can_leave_reference=is_authenticated_user(),
    )


@app.route("/stores/<shop_id>/reference", methods=["POST"])
def save_store_reference(shop_id: str) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para dejar una referencia.", "error")
        return redirect(url_for("register_page"))

    profile = build_store_profile_context(shop_id)
    if not profile:
        flash("Tienda no encontrada.", "error")
        return redirect(url_for("stores_catalog_page"))

    note = request.form.get("reference_text", "").strip()
    if not note:
        flash("Escribe un comentario para guardar la referencia.", "error")
        return redirect(url_for("store_detail_page", shop_id=shop_id))

    try:
        rating = max(1, min(5, int(request.form.get("rating", "5"))))
    except ValueError:
        rating = 5

    author = current_user_identity()
    references = load_store_references()
    references.append(
        {
            "shop_id": (profile.get("shop_id") or "").strip().lower(),
            "shop_name": profile.get("name") or "Tienda",
            "author_user_id": author.get("user_id"),
            "author_email": author.get("email"),
            "author_name": author.get("name"),
            "text": note,
            "rating": rating,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_store_references(references)
    flash("Referencia guardada correctamente.", "success")
    return redirect(url_for("store_detail_page", shop_id=shop_id))


@app.route("/products/create", methods=["POST"])
def create_product() -> Any:
    if not is_admin_user():
        flash("No tienes permisos para crear productos.", "error")
        return redirect(url_for("products_catalog_page"))

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("El nombre del producto es obligatorio.", "error")
        return redirect(url_for("products_page"))

    products = load_products()
    products.append(
        {
            "name": name,
            "category": request.form.get("category", "").strip(),
            "brand": request.form.get("brand", "").strip(),
            "description": description,
            "quantity": normalize_quantity(request.form.get("quantity", "0")),
            "image": request.form.get("image", "").strip(),
            "price": normalize_price(request.form.get("price", "0")),
        }
    )
    save_products(products)
    flash("Producto creado.", "success")
    return redirect(url_for("products_page"))


@app.route("/products/<int:product_index>/update", methods=["POST"])
def update_product(product_index: int) -> Any:
    if not is_admin_user():
        flash("No tienes permisos para actualizar productos.", "error")
        return redirect(url_for("products_catalog_page"))

    products = load_products()
    if product_index < 0 or product_index >= len(products):
        flash("Producto no encontrado.", "error")
        return redirect(url_for("products_page"))

    name = request.form.get("name", "").strip()
    if not name:
        flash("El nombre del producto es obligatorio.", "error")
        return redirect(url_for("products_page"))

    products[product_index] = {
        "name": name,
        "category": request.form.get("category", "").strip(),
        "brand": request.form.get("brand", "").strip(),
        "description": request.form.get("description", "").strip(),
        "quantity": normalize_quantity(request.form.get("quantity", "0")),
        "image": request.form.get("image", "").strip(),
        "price": normalize_price(request.form.get("price", "0")),
    }
    save_products(products)
    flash("Producto actualizado.", "success")
    return redirect(url_for("products_page"))


@app.route("/products/<int:product_index>/delete", methods=["POST"])
def delete_product(product_index: int) -> Any:
    if not is_admin_user():
        flash("No tienes permisos para eliminar productos.", "error")
        return redirect(url_for("products_catalog_page"))

    products = load_products()
    if product_index < 0 or product_index >= len(products):
        flash("Producto no encontrado.", "error")
        return redirect(url_for("products_page"))

    products.pop(product_index)
    save_products(products)
    flash("Producto eliminado.", "success")
    return redirect(url_for("products_page"))


@app.route("/cart", methods=["GET"])
def cart_page() -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para ver tu carrito.", "error")
        return redirect(url_for("register_page"))

    return render_template(
        "cart_page.html",
        search_query="",
        cart_items=get_current_cart_items(),
    )


@app.route("/cart/add/<int:product_index>", methods=["POST"])
def add_to_cart(product_index: int) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para guardar productos en el carrito.", "error")
        return redirect(url_for("register_page"))

    products = load_products()
    if product_index < 0 or product_index >= len(products):
        flash("Producto no encontrado.", "error")
        return redirect(url_for("products_catalog_page"))

    product = products[product_index]
    stock = int(product.get("quantity") or 0)
    quantity = normalize_quantity(request.form.get("quantity", "1"))
    quantity = max(1, quantity)

    if stock <= 0:
        flash("Este producto no tiene stock disponible.", "error")
        return redirect(url_for("products_catalog_page"))

    quantity = min(quantity, stock)
    owner = current_user_identity()
    cart = load_cart()
    cart.append(
        {
            "user_email": owner.get("email"),
            "user_name": owner.get("name"),
            "name": product.get("name"),
            "brand": product.get("brand", ""),
            "price": product.get("price", 0),
            "image": product.get("image", ""),
            "quantity": quantity,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_cart(cart)
    flash("Producto guardado en el carrito.", "success")
    return redirect(url_for("products_catalog_page"))


@app.route("/cart/<int:item_index>/delete", methods=["POST"])
def delete_cart_item(item_index: int) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para editar tu carrito.", "error")
        return redirect(url_for("register_page"))

    cart = load_cart()
    if item_index < 0 or item_index >= len(cart):
        flash("Producto no encontrado en el carrito.", "error")
        return redirect(url_for("cart_page"))

    current_email = current_user_identity().get("email", "")
    item = cart[item_index]
    if (item.get("user_email") or "").strip().lower() != current_email:
        flash("No puedes eliminar productos de otro carrito.", "error")
        return redirect(url_for("cart_page"))

    cart.pop(item_index)
    save_cart(cart)
    flash("Producto eliminado del carrito.", "success")
    return redirect(url_for("cart_page"))


@app.route("/register/local", methods=["POST"])
def register_local() -> Any:
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if len(name) < 2:
        flash("Ingresa un nombre valido.", "error")
        return redirect(url_for("register_page"))

    if "@" not in email or "." not in email:
        flash("Ingresa un correo valido.", "error")
        return redirect(url_for("register_page"))

    if len(password) < 6:
        flash("La clave debe tener al menos 6 caracteres.", "error")
        return redirect(url_for("register_page"))

    if password != confirm_password:
        flash("Las claves no coinciden.", "error")
        return redirect(url_for("register_page"))

    if find_user_by_email(email):
        flash("Ya existe una cuenta con ese correo.", "error")
        return redirect(url_for("register_page"))

    users = get_users()
    user_id = uuid4().hex[:12]
    users.append(
        {
            "user_id": user_id,
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "provider": "local",
            "role": "usuario",
            "country": normalize_country(request.form.get("country")),
        }
    )
    save_users(users)
    session["user"] = {
        "user_id": user_id,
        "name": name,
        "email": email,
        "provider": "local",
        "role": "usuario",
        "country": normalize_country(request.form.get("country")),
    }
    flash("Cuenta creada correctamente.", "success")
    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login_local() -> Any:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = find_user_by_email(email)

    if not user or not check_password_hash(user.get("password_hash", ""), password):
        flash("Correo o clave incorrectos.", "error")
        return redirect(url_for("register_page"))

    session["user"] = {
        "user_id": user.get("user_id"),
        "name": user.get("name") or email,
        "email": user.get("email"),
        "provider": user.get("provider") or "local",
        "role": normalize_role(user.get("role")),
        "country": normalize_country(user.get("country")),
    }
    flash("Sesion iniciada.", "success")
    return redirect(url_for("index"))


@app.route("/saved", methods=["GET"])
def saved_cards_page() -> str:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para ver tu listado.", "error")
        return redirect(url_for("register_page"))

    saved_cards = [build_saved_card_view(item, index) for index, item in enumerate(load_saved_cards())]
    current_email = current_user_identity().get("email", "")
    saved_cards = [
        item for item in saved_cards
        if (item.get("user_email") or "").strip().lower() == current_email
    ]
    status_filter = request.args.get("status", "").strip().lower()
    rarity_filter = request.args.get("rarity", "").strip().lower()
    available_rarities = sorted({(item.get("rarity") or "").lower() for item in saved_cards if item.get("rarity")})

    if status_filter:
        saved_cards = [item for item in saved_cards if (item.get("intent") or "").lower() == status_filter]
    if rarity_filter:
        saved_cards = [item for item in saved_cards if (item.get("rarity") or "").lower() == rarity_filter]

    return render_template(
        "saved_manage_grid.html",
        search_query="",
        saved_cards=saved_cards,
        status_filter=status_filter,
        rarity_filter=rarity_filter,
        available_rarities=available_rarities,
    )


@app.route("/users", methods=["GET"])
def users_page() -> Any:
    if not is_admin_user():
        flash("Solo el perfil adm puede acceder al mantenedor de usuarios.", "error")
        return redirect(url_for("index"))

    return render_template(
        "users_page.html",
        search_query="",
        users=get_users(),
        role_options=ROLE_OPTIONS,
    )


@app.route("/user/<user_id>", methods=["GET"])
def user_profile_page(user_id: str) -> Any:
    profile = build_user_profile_context(user_id)
    if not profile:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("index"))

    current_user_id = current_user_identity().get("user_id", "")
    can_leave_reference = is_authenticated_user() and current_user_id and current_user_id != (profile["user"].get("user_id") or "").strip().lower()

    return render_template(
        "user_profile_page.html",
        search_query="",
        profile=profile,
        can_leave_reference=can_leave_reference,
    )


@app.route("/user/<user_id>/reference", methods=["POST"])
def save_user_reference(user_id: str) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para dejar una referencia.", "error")
        return redirect(url_for("register_page"))

    profile = build_user_profile_context(user_id)
    if not profile:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("index"))

    current_user = current_user_identity()
    target_user_id = (profile["user"].get("user_id") or "").strip().lower()
    normalized_target = (profile["user"].get("email") or "").strip().lower()
    if current_user.get("user_id") == target_user_id:
        flash("No puedes dejar una referencia sobre tu propio perfil.", "error")
        return redirect(url_for("user_profile_page", user_id=target_user_id))

    note = request.form.get("reference_text", "").strip()
    if not note:
        flash("Escribe un comentario para guardar la referencia.", "error")
        return redirect(url_for("user_profile_page", user_id=target_user_id))

    try:
        rating = max(1, min(5, int(request.form.get("rating", "5"))))
    except ValueError:
        rating = 5

    references = load_user_references()
    references.append(
        {
            "target_user_id": target_user_id,
            "target_email": normalized_target,
            "target_name": profile["user"].get("name") or normalized_target,
            "author_user_id": current_user.get("user_id"),
            "author_email": current_user.get("email"),
            "author_name": current_user.get("name"),
            "text": note,
            "rating": rating,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_user_references(references)
    flash("Referencia guardada correctamente.", "success")
    return redirect(url_for("user_profile_page", user_id=target_user_id))


@app.route("/messages", methods=["GET"])
def messages_page() -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para ver tus mensajes.", "error")
        return redirect(url_for("register_page"))

    mark_messages_as_read()
    return render_template(
        "messages_page.html",
        search_query="",
        threads=build_message_threads(),
    )


@app.route("/messages/send", methods=["POST"])
def send_message() -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para contactar a otro usuario.", "error")
        return redirect(url_for("register_page"))

    sender = current_user_identity()
    receiver_id = request.form.get("receiver_user_id", "").strip().lower()
    receiver = find_user_by_id(receiver_id)
    if not receiver:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("community_cards_page"))

    if sender.get("user_id") == receiver_id:
        flash("No puedes enviarte mensajes a ti mismo.", "error")
        return redirect(url_for("community_cards_page"))

    text = request.form.get("message_text", "").strip()
    if not text:
        flash("Escribe un mensaje antes de guardar.", "error")
        return redirect(url_for("community_cards_page"))

    card_name = request.form.get("card_name", "").strip()
    messages = load_messages()
    messages.append(
        {
            "message_id": uuid4().hex[:16],
            "sender_user_id": sender.get("user_id"),
            "sender_name": sender.get("name"),
            "receiver_user_id": receiver_id,
            "receiver_name": receiver.get("name") or "Usuario",
            "card_id": request.form.get("card_id", "").strip(),
            "card_name": card_name,
            "text": text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "read": False,
        }
    )
    save_messages(messages)
    flash_message_delivery(notify_message_recipient(receiver, sender, card_name))
    return_card_id = request.form.get("return_card_id", "").strip()
    if return_card_id:
        return redirect(url_for("card_detail", card_id=return_card_id))
    return redirect(url_for("community_cards_page", status=request.form.get("return_status", "").strip(), rarity=request.form.get("return_rarity", "").strip()))


@app.route("/messages/reply", methods=["POST"])
def reply_message() -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para responder mensajes.", "error")
        return redirect(url_for("register_page"))

    sender = current_user_identity()
    receiver_id = request.form.get("receiver_user_id", "").strip().lower()
    receiver = find_user_by_id(receiver_id)
    if not receiver:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("messages_page"))

    text = request.form.get("message_text", "").strip()
    if not text:
        flash("Escribe un mensaje antes de responder.", "error")
        return redirect(url_for("messages_page"))

    messages = load_messages()
    messages.append(
        {
            "message_id": uuid4().hex[:16],
            "sender_user_id": sender.get("user_id"),
            "sender_name": sender.get("name"),
            "receiver_user_id": receiver_id,
            "receiver_name": receiver.get("name") or "Usuario",
            "card_id": "",
            "card_name": "",
            "text": text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "read": False,
        }
    )
    save_messages(messages)
    flash_message_delivery(notify_message_recipient(receiver, sender))
    return redirect(url_for("messages_page"))


SEARCH_HISTORY_PAGE_SIZE = 10


@app.route("/search-history", methods=["GET"])
def search_history_page() -> Any:
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1

    history = [build_search_history_view(item) for item in reversed(load_search_log())]
    total_items = len(history)
    total_pages = max(1, (total_items + SEARCH_HISTORY_PAGE_SIZE - 1) // SEARCH_HISTORY_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start_index = (page - 1) * SEARCH_HISTORY_PAGE_SIZE
    end_index = start_index + SEARCH_HISTORY_PAGE_SIZE
    paged_history = history[start_index:end_index]

    return render_template(
        "search_history_page.html",
        search_query="",
        history=paged_history,
        page=page,
        total_pages=total_pages,
    )


@app.route("/search-history/dashboard", methods=["GET"])
def search_history_dashboard_page() -> Any:
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    color_filter = request.args.get("color", "").strip().upper()
    legality_filter = request.args.get("legality", "").strip().lower()

    history = load_search_log()
    filtered_history = filter_search_history_by_date(history, start_date, end_date)
    dashboard_items = build_search_dashboard(filtered_history, color_filter, legality_filter)

    return render_template(
        "search_history_dashboard.html",
        search_query="",
        dashboard_items=dashboard_items,
        start_date=start_date,
        end_date=end_date,
        color_filter=color_filter,
        legality_filter=legality_filter,
        color_options=COLOR_OPTIONS,
        legality_options=LEGALITY_OPTIONS,
    )


@app.route("/users/<int:user_index>/update", methods=["POST"])
def update_user_profile(user_index: int) -> Any:
    if not is_admin_user():
        flash("No tienes permisos para modificar usuarios.", "error")
        return redirect(url_for("index"))

    users = get_users()
    if user_index < 0 or user_index >= len(users):
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("users_page"))

    next_role = normalize_role(request.form.get("role"))
    admin_count = sum(1 for user in users if normalize_role(user.get("role")) == "adm")
    if normalize_role(users[user_index].get("role")) == "adm" and next_role != "adm" and admin_count <= 1:
        flash("Debe existir al menos un usuario con perfil adm.", "error")
        return redirect(url_for("users_page"))

    users[user_index]["role"] = next_role
    save_users(users)
    sync_session_user(users[user_index].get("email"))
    flash("Perfil actualizado.", "success")
    return redirect(url_for("users_page"))


@app.route("/auth/google")
def auth_google() -> Any:
    if not google_auth_ready():
        flash("Configura GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET para habilitar Google.", "error")
        return redirect(url_for("register_page"))

    redirect_uri = url_for("auth_google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback() -> Any:
    if not google_auth_ready():
        flash("La autenticacion con Google no esta configurada.", "error")
        return redirect(url_for("register_page"))

    token = google.authorize_access_token()
    user_info = token.get("userinfo")
    if not user_info:
        user_info = google.userinfo()

    stored_user = find_user_by_email(user_info.get("email", ""))
    role = normalize_role(stored_user.get("role") if stored_user else "usuario")

    if not stored_user and user_info.get("email"):
        users = get_users()
        new_user_id = uuid4().hex[:12]
        users.append(
            {
                "user_id": new_user_id,
                "name": user_info.get("name") or user_info.get("email") or "Usuario Google",
                "email": user_info.get("email"),
                "password_hash": "",
                "provider": "google",
                "role": "usuario",
            }
        )
        save_users(users)
        stored_user = find_user_by_email(user_info.get("email", ""))
        role = normalize_role((stored_user or {}).get("role") if stored_user else "usuario")

    session["user"] = {
        "user_id": (stored_user or {}).get("user_id"),
        "name": user_info.get("name") or user_info.get("email") or "Usuario Google",
        "email": user_info.get("email"),
        "picture": user_info.get("picture"),
        "provider": "google",
        "role": role,
        "country": normalize_country((stored_user or {}).get("country")),
    }
    flash("Sesion iniciada con Google.", "success")
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout() -> Any:
    session.pop("user", None)
    flash("Sesion cerrada.", "success")
    return redirect(url_for("index"))


@app.route("/api/autocomplete", methods=["GET"])
def autocomplete() -> tuple[Any, int] | Any:
    query = request.args.get("q", "").strip()

    if len(query) < 3:
        return jsonify({"query": query, "items": []})

    try:
        return jsonify({"query": query, "items": autocomplete_cards(query)})
    except RuntimeError as exc:
        return jsonify({"query": query, "items": [], "error": str(exc)}), 502


@app.route("/card/<card_id>", methods=["GET"])
def card_detail(card_id: str) -> str:
    try:
        selected_id = request.args.get("edition", "").strip() or card_id
        selected_card_raw = get_card(selected_id)
        card = build_card_detail(selected_card_raw)
        editions = get_card_editions(selected_card_raw, selected_id)
        viewer_id = current_user_identity().get("user_id") if is_authenticated_user() else ""
        saved_status = find_saved_status(selected_card_raw.get("name", ""), viewer_id)
        return render_template(
            "card_page.html",
            card=card,
            editions=editions,
            saved_status=saved_status,
            error=None,
            search_query="",
        )
    except RuntimeError as exc:
        return render_template(
            "card_page.html",
            card=None,
            editions=[],
            saved_status=None,
            error=str(exc),
            search_query="",
        ), 502


@app.route("/card/<card_id>/save", methods=["POST"])
def save_card_entry(card_id: str) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para guardar cartas.", "error")
        return redirect(url_for("register_page"))

    try:
        selected_id = request.form.get("edition", "").strip() or card_id
        selected_card_raw = get_card(selected_id)
        owner = current_user_identity()

        quantity_raw = request.form.get("quantity", "1").strip()
        try:
            quantity = max(1, min(4, int(quantity_raw)))
        except ValueError:
            quantity = 1

        intent = request.form.get("intent", "busco").strip().lower()
        if intent not in {"busco", "ofrezco"}:
            intent = "busco"

        saved_cards = load_saved_cards()
        saved_cards.append(
            {
                "card_id": selected_card_raw.get("id"),
                "name": selected_card_raw.get("name"),
                "edition": selected_card_raw.get("set_name"),
                "edition_code": (selected_card_raw.get("set") or "").upper(),
                "rarity": selected_card_raw.get("rarity"),
                "quantity": quantity,
                "intent": intent,
                "note": request.form.get("note", "").strip(),
                "transaction_completed": False,
                "user_id": owner.get("user_id"),
                "user_name": owner.get("name"),
                "user_email": owner.get("email"),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        save_saved_cards(saved_cards)
        flash("Carta guardada correctamente.", "success")
    except RuntimeError as exc:
        flash(str(exc), "error")

    return redirect(url_for("card_detail", card_id=card_id, edition=request.form.get("edition", card_id)))


@app.route("/saved/<int:item_index>/update", methods=["POST"])
def update_saved_card(item_index: int) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para editar registros.", "error")
        return redirect(url_for("register_page"))

    saved_cards = load_saved_cards()
    if item_index < 0 or item_index >= len(saved_cards):
        flash("Registro no encontrado.", "error")
        return redirect(url_for("saved_cards_page"))

    current_item = saved_cards[item_index]
    if not can_manage_saved_item(current_item):
        flash("No tienes permisos para editar este registro.", "error")
        return redirect(url_for("saved_cards_page"))

    selected_id = request.form.get("edition", "").strip() or current_item.get("card_id", "")

    try:
        selected_card_raw = get_card(selected_id)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("saved_cards_page"))

    quantity_raw = request.form.get("quantity", "1").strip()
    try:
        quantity = max(1, min(4, int(quantity_raw)))
    except ValueError:
        quantity = 1

    intent = request.form.get("intent", "busco").strip().lower()
    if intent not in {"busco", "ofrezco"}:
        intent = "busco"

    owner = {
        "user_id": (current_item.get("user_id") or current_user_identity().get("user_id")).strip().lower(),
        "name": current_item.get("user_name") or current_user_identity().get("name"),
        "email": (current_item.get("user_email") or current_user_identity().get("email")).strip().lower(),
    }

    saved_cards[item_index] = {
        "card_id": selected_card_raw.get("id"),
        "name": selected_card_raw.get("name"),
        "edition": selected_card_raw.get("set_name"),
        "edition_code": (selected_card_raw.get("set") or "").upper(),
        "rarity": selected_card_raw.get("rarity"),
        "quantity": quantity,
        "intent": intent,
        "note": request.form.get("note", "").strip(),
        "transaction_completed": bool(current_item.get("transaction_completed", False)),
        "user_id": owner.get("user_id"),
        "user_name": owner.get("name"),
        "user_email": owner.get("email"),
        "created_at": current_item.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_saved_cards(saved_cards)
    flash("Registro actualizado.", "success")
    return redirect(url_for("saved_cards_page"))


@app.route("/saved/<int:item_index>/transaction", methods=["POST"])
def toggle_saved_transaction(item_index: int) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para actualizar tu listado.", "error")
        return redirect(url_for("register_page"))

    saved_cards = load_saved_cards()
    if item_index < 0 or item_index >= len(saved_cards):
        flash("Registro no encontrado.", "error")
        return redirect(url_for("saved_cards_page"))

    current_item = saved_cards[item_index]
    if not can_manage_saved_item(current_item):
        flash("No tienes permisos para actualizar este registro.", "error")
        return redirect(url_for("saved_cards_page"))

    current_item["transaction_completed"] = not bool(current_item.get("transaction_completed", False))
    save_saved_cards(saved_cards)
    message = "Transaccion marcada como terminada." if current_item["transaction_completed"] else "Publicacion reactivada."
    flash(message, "success")
    return redirect(url_for("saved_cards_page"))


@app.route("/saved/<int:item_index>/delete", methods=["POST"])
def delete_saved_card(item_index: int) -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para eliminar registros.", "error")
        return redirect(url_for("register_page"))

    saved_cards = load_saved_cards()
    if item_index < 0 or item_index >= len(saved_cards):
        flash("Registro no encontrado.", "error")
        return redirect(url_for("saved_cards_page"))

    if not can_manage_saved_item(saved_cards[item_index]):
        flash("No tienes permisos para eliminar este registro.", "error")
        return redirect(url_for("saved_cards_page"))

    saved_cards.pop(item_index)
    save_saved_cards(saved_cards)
    flash("Registro eliminado.", "success")
    return redirect(url_for("saved_cards_page"))


if __name__ == "__main__":
    app.run(debug=True)
