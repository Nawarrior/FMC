from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import re
import secrets
import smtplib
from typing import Any
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import json
import time

from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge

from mailer import send_email
from news_content import defaults as news_defaults, parse_content, display_content, TEXT_LIMITS as NEWS_TEXT_LIMITS, import_json as import_news_json, video_details
from database import (
    add_card_notifications, count_unread_card_notifications, initialize_database,
    load_card_notifications, load_collection, mark_card_notifications_read,
    migrate_json_files_to_db, save_collection, get_connection,
)


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
PASSWORD_RESET_EXPIRATION_HOURS = 1
PASSWORD_RESET_RATE_LIMIT = 5
PASSWORD_RESET_RATE_LIMIT_MINUTES = 60
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
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"}
app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
app.config["GOOGLE_DISCOVERY_URL"] = "https://accounts.google.com/.well-known/openid-configuration"
app.config["GOOGLE_LOGIN_ENABLED"] = os.environ.get("GOOGLE_LOGIN_ENABLED", "false").strip().lower() in {"1", "true", "yes"}

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
    return bool(app.config["GOOGLE_LOGIN_ENABLED"] and app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"])


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def validate_csrf_token() -> None:
    if request.method != "POST":
        return
    if request.endpoint == "email_digest_trigger":
        return  # This endpoint authenticates with a private scheduler token.
    if request.endpoint in {"bulk_import_page", "decks.import_cards"}:
        request.max_content_length = 2 * 1024 * 1024 + 65536
    if request.endpoint == "news_import":
        request.max_content_length = 1024 * 1024 + 65536
        request.max_form_memory_size = 1024 * 1024 + 65536
    if request.endpoint == "tournaments.manage":
        request.max_content_length = 2 * 1024 * 1024 + 65536
        request.max_form_memory_size = 2 * 1024 * 1024 + 65536

    expected_token = session.get("_csrf_token")
    submitted_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected_token or not submitted_token or not secrets.compare_digest(expected_token, submitted_token):
        abort(400)


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
        "unread_card_notification_count": count_unread_card_notifications(current_user_identity()["user_id"])
        if is_authenticated_user() and current_user_identity()["user_id"] else 0,
        "csrf_token": csrf_token,
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


def card_similarity_features(card: dict[str, Any]) -> tuple[set[str], set[str], set[str], set[str], float]:
    face = (card.get("card_faces") or [{}])[0]
    type_line = (face.get("type_line") or card.get("type_line") or "").lower()
    main_type, _, subtype = type_line.partition("—")
    types = set(main_type.split()) - {"legendary", "basic", "snow", "world", "ongoing"}
    colors = set(card.get("colors", face.get("colors", [])))
    keywords = {keyword.lower() for keyword in card.get("keywords", [])}
    return types, set(subtype.split()), colors, keywords, float(card.get("cmc") or 0)


def card_rules_words(card: dict[str, Any]) -> list[str]:
    text = card.get("oracle_text") or " ".join(face.get("oracle_text", "") for face in card.get("card_faces", []))
    # A card referring to itself should not make its rules look different.
    for name in [card.get("name"), *[face.get("name") for face in card.get("card_faces", [])]]:
        if name:
            text = re.sub(re.escape(name), "", text, flags=re.IGNORECASE)
    forms = {
        "discards": "discard", "discarded": "discard", "discarding": "discard",
        "draws": "draw", "drawing": "draw", "drawn": "draw",
        "destroys": "destroy", "destroyed": "destroy",
        "exiles": "exile", "exiled": "exile", "exiling": "exile",
        "counters": "counter", "countered": "counter",
        "creates": "create", "created": "create",
        "returns": "return", "returned": "return",
        "searches": "search", "searched": "search",
        "gains": "gain", "gained": "gain", "loses": "lose", "lost": "lose",
        "sacrifices": "sacrifice", "sacrificed": "sacrifice",
        "reveals": "reveal", "revealed": "reveal",
        "chooses": "choose", "chosen": "choose",
        "cards": "card", "creatures": "creature", "lands": "land",
        "deals": "deal", "dealt": "deal",
    }
    return [forms.get(word, word) for word in re.findall(r"[a-z]+", text.lower())]


def card_effect_terms(card: dict[str, Any]) -> set[str]:
    return set(card_rules_words(card)) & {
        "damage", "draw", "destroy", "exile", "counter", "create", "return",
        "search", "gain", "discard", "sacrifice", "equip", "enchant", "mana",
    }


def card_function_tags(card: dict[str, Any]) -> set[str]:
    words = set(card_rules_words(card))
    tags = set()
    if {"reveal", "hand", "choose"} <= words:
        tags.add("choose_from_hand")
        if "discard" in words:
            tags.add("chosen_discard")
    if {"discard", "random"} <= words:
        tags.add("random_discard")
    if {"draw", "discard"} <= words:
        tags.add("draw_and_discard")
    return tags


def card_hand_choice_types(card: dict[str, Any]) -> set[str]:
    if "choose_from_hand" not in card_function_tags(card):
        return set()
    text = card.get("oracle_text") or " ".join(face.get("oracle_text", "") for face in card.get("card_faces", []))
    choice = re.search(r"\bchoose\b([^.;]*?)\bcard\b", text.lower())
    if not choice:
        return set()
    card_types = {"artifact", "battle", "creature", "enchantment", "instant", "land", "planeswalker", "sorcery"}
    clause = choice.group(1)
    excluded = {kind for kind in card_types if re.search(rf"\bnon{kind}\b", clause)}
    included = {kind for kind in card_types if re.search(rf"\b{kind}\b", clause)}
    return (included or card_types) - excluded


def search_similar_candidates(query: str) -> list[dict[str, Any]]:
    payload = fetch_json(f"/cards/search?q={quote(query)}&unique=cards&order=name")
    candidates = list(payload.get("data", []))
    # Rank the full result set so names late in the alphabet are considered too.
    while payload.get("has_more") and payload.get("next_page"):
        time.sleep(0.1)
        payload = fetch_json_from_url(payload["next_page"])
        candidates.extend(payload.get("data", []))
    return candidates


def get_similar_cards(card: dict[str, Any]) -> list[dict[str, Any]]:
    types, subtypes, colors, keywords, mana_value = card_similarity_features(card)
    type_query = "(" + " or ".join(f"t:{json.dumps(value)}" for value in sorted(types)) + ")"
    if not types:
        return []
    color_query = f"c={''.join(sorted(colors)).lower() or 'c'}"
    traits = [f"t:{json.dumps(value)}" for value in sorted(subtypes)]
    traits += [f"keyword:{json.dumps(value)}" for value in sorted(keywords)]
    effects = card_effect_terms(card)
    functions = card_function_tags(card)
    # Functional roles are more precise than a single shared rules word.
    if "chosen_discard" in functions:
        traits = ["(o:discard o:hand o:choose)"]
    elif "choose_from_hand" in functions:
        traits = ["(o:hand o:choose)"]
    else:
        traits += [f"o:{json.dumps(value)}" for value in sorted(effects)]
    exact_query = f"{type_query} {color_query} cmc={mana_value:g}"
    close_query = f"{type_query} {color_query} cmc>={max(0, mana_value - 2):g} cmc<={mana_value + 2:g}"
    if traits:
        trait_query = " (" + " or ".join(traits) + ")"
        exact_query += trait_query
        close_query += trait_query
    queries = [exact_query, close_query]
    if traits:
        queries.append(f"{type_query} {color_query} (" + " or ".join(traits) + ")")
    queries += [f"{type_query} {color_query}", type_query]
    candidates: dict[str, dict[str, Any]] = {}
    selected_name = (card.get("name") or "").casefold()
    for index, query in enumerate(dict.fromkeys(queries)):
        if index:
            time.sleep(0.1)
        query += f" -!{json.dumps(card.get('name', ''), ensure_ascii=False)}"
        try:
            matches = search_similar_candidates(query)
        except RuntimeError as exc:
            if isinstance(exc.__cause__, HTTPError) and exc.__cause__.code == 404:
                continue  # No matches for this tier; try the broader query.
            raise
        for candidate in matches:
            name = (candidate.get("name") or "").casefold()
            if not candidate.get("id") or not name or name == selected_name:
                continue
            if card.get("oracle_id") and candidate.get("oracle_id") == card["oracle_id"]:
                continue
            candidates.setdefault(name, candidate)
        if len(candidates) >= 3:
            break

    stop_words = {"a", "an", "the", "of", "to", "it", "that", "this", "their", "they",
                  "you", "your", "and", "or", "from", "for", "with", "then", "may", "as", "is"}
    rules = set(card_rules_words(card)) - stop_words
    choice_types = card_hand_choice_types(card)

    def overlap(left: set[str], right: set[str]) -> float:
        return len(left & right) / len(left | right) if left or right else 0

    def similarity(candidate: dict[str, Any]) -> tuple[float, str]:
        other_types, other_subtypes, other_colors, other_keywords, other_mana = card_similarity_features(candidate)
        color_union = colors | other_colors
        color_match = len(colors & other_colors) / len(color_union) if color_union else 1
        other_functions = card_function_tags(candidate)
        score = (4 * overlap(types, other_types) + 6 * overlap(subtypes, other_subtypes)
                 + 6 * color_match + 8 * overlap(keywords, other_keywords)
                 + 12 * overlap(effects, card_effect_terms(candidate))
                 + 24 * overlap(rules, set(card_rules_words(candidate)) - stop_words)
                 + 20 * overlap(functions, other_functions)
                 + 24 * overlap(choice_types, card_hand_choice_types(candidate))
                 - 6 * min(abs(mana_value - other_mana), 6))
        return -score, candidate.get("name", "")

    return [summarize_card(candidate) for candidate in sorted(candidates.values(), key=similarity)[:3]]


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


def load_password_reset_tokens() -> list[dict[str, Any]]:
    return load_collection("password_reset_tokens")


def save_password_reset_tokens(items: list[dict[str, Any]]) -> None:
    save_collection("password_reset_tokens", items)


def load_password_reset_requests() -> list[dict[str, Any]]:
    return load_collection("password_reset_requests")


def save_password_reset_requests(items: list[dict[str, Any]]) -> None:
    save_collection("password_reset_requests", items)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(user: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(32)
    created_at = datetime.now()
    tokens = []
    for item in load_password_reset_tokens():
        expires_at = parse_search_timestamp(item.get("expires_at"))
        if item.get("used_at") or (expires_at and expires_at > created_at):
            tokens.append(item)

    tokens.append(
        {
            "token_hash": hash_reset_token(token),
            "user_email": (user.get("email") or "").strip().lower(),
            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": (created_at + timedelta(hours=PASSWORD_RESET_EXPIRATION_HOURS)).strftime("%Y-%m-%d %H:%M:%S"),
            "used_at": "",
        }
    )
    save_password_reset_tokens(tokens)
    return token


def password_reset_rate_limited(email: str, requester: str) -> bool:
    now = datetime.now()
    window_start = now - timedelta(minutes=PASSWORD_RESET_RATE_LIMIT_MINUTES)
    requests = []
    recent_matches = 0
    normalized_email = email.strip().lower()

    for item in load_password_reset_requests():
        created_at = parse_search_timestamp(item.get("created_at"))
        if not created_at or created_at < window_start:
            continue
        requests.append(item)
        if (item.get("email") or "").strip().lower() == normalized_email or (item.get("requester") or "") == requester:
            recent_matches += 1

    requests.append(
        {
            "email": normalized_email,
            "requester": requester,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_password_reset_requests(requests)
    return recent_matches >= PASSWORD_RESET_RATE_LIMIT


def find_valid_password_reset_token(token: str) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    token_hash = hash_reset_token(token)
    now = datetime.now()
    for index, item in enumerate(load_password_reset_tokens()):
        expires_at = parse_search_timestamp(item.get("expires_at"))
        if (
            item.get("token_hash") == token_hash
            and not item.get("used_at")
            and expires_at
            and expires_at > now
        ):
            return index, item
    return None, None


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


def notify_card_matches(card: dict[str, Any], intent: str, actor: dict[str, str] | None = None,
                        *, users: list | None = None, saved_cards: list | None = None,
                        notification_sink: list | None = None) -> int:
    actor = actor if actor is not None else current_user_identity()
    actor_id = (actor.get("user_id") or "").strip().lower()
    actor_email = (actor.get("email") or "").strip().lower()
    card_name = (card.get("name") or "").strip()
    if not actor_id or not actor_email or not card_name or intent not in {"busco", "ofrezco"}:
        return 0

    target_intent = "ofrezco" if intent == "busco" else "busco"
    kind = "searched" if intent == "busco" else "offered"
    users = users if users is not None else load_users()
    users_by_id = {(user.get("user_id") or "").strip().lower(): user for user in users if user.get("user_id")}
    users_by_email = {(user.get("email") or "").strip().lower(): user for user in users if user.get("email")}
    recipients = set()
    now = datetime.now()
    notifications = []
    for item in (saved_cards if saved_cards is not None else load_saved_cards()):
        if ((item.get("name") or "").strip().casefold() != card_name.casefold()
                or (item.get("intent") or "").strip().lower() != target_intent
                or item.get("transaction_completed", False)
                or int(item.get("quantity", 1) or 0) <= 0):
            continue
        owner = users_by_id.get((item.get("user_id") or "").strip().lower())
        owner = owner or users_by_email.get((item.get("user_email") or "").strip().lower())
        if not owner:
            continue
        receiver_id = (owner.get("user_id") or "").strip().lower()
        if (not receiver_id or receiver_id == actor_id or receiver_id in recipients
                or (owner.get("email") or "").strip().lower() == actor_email):
            continue
        recipients.add(receiver_id)
        # One notice per person, card and direction each day, including across editions.
        key = json.dumps([now.date().isoformat(), actor_id, receiver_id, card_name.casefold(), kind])
        notifications.append({
            "notification_id": uuid4().hex,
            "dedup_key": hashlib.sha256(key.encode("utf-8")).hexdigest(),
            "receiver_user_id": receiver_id,
            "actor_user_id": actor_id,
            "actor_name": actor.get("name") or "Un usuario",
            "card_id": card.get("id") or card.get("card_id") or item.get("card_id"),
            "card_name": card_name,
            "kind": kind,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "read": False,
        })
    if notification_sink is not None:
        notification_sink.extend(notifications)
        return len(notifications)
    return add_card_notifications(notifications) if notifications else 0


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


def send_password_reset_email(user: dict[str, Any], reset_url: str) -> bool:
    return send_email(
        user.get("email") or "", "Recupera tu clave de Findmycard",
        f"Hola {user.get('name') or 'usuario'},\n\n"
        "Recibimos una solicitud para cambiar tu clave de Findmycard.\n"
        f"Este enlace vence en {PASSWORD_RESET_EXPIRATION_HOURS} hora(s):\n\n"
        f"{reset_url}\n\n"
        "Si no pediste este cambio, puedes ignorar este correo.\n",
    )


def notify_password_reset(user: dict[str, Any], reset_url: str) -> bool:
    try:
        return send_password_reset_email(user, reset_url)
    except (OSError, smtplib.SMTPException, ValueError):
        app.logger.exception("No se pudo enviar el correo de recuperacion de clave.")
        return False


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


def build_saved_card_view(item: dict[str, Any], index: int, hydrate: bool = True) -> dict[str, Any]:
    selected_id = item.get("card_id", "")
    editions: list[dict[str, Any]] = []
    image_url: str | None = item.get("image_url")
    if not image_url and selected_id and not hydrate:
        image_url = f"{SCRYFALL_API}/cards/{quote(selected_id)}?format=image&version=normal"
    rarity = item.get("rarity", "")
    owner_user_id = (item.get("user_id") or "").strip().lower()

    if selected_id and hydrate:
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
        build_saved_card_view(item, index, hydrate=False)
        for index, item in enumerate(load_saved_cards())
        if ((item.get("user_id") or "").strip().lower() == normalized_user_id
            or (not item.get("user_id") and (item.get("user_email") or "").strip().lower() == normalized_email))
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
    with get_connection() as connection:
        published_deck_count = connection.execute("SELECT COUNT(*) FROM decks WHERE user_id = ? AND published = 1", (normalized_user_id,)).fetchone()[0]

    return {
        "user": user,
        "country": country,
        "saved_cards": saved_cards,
        "references": references,
        "average_rating": average_rating,
        "busco_count": sum(item.get("intent") == "busco" for item in saved_cards),
        "ofrezco_count": sum(item.get("intent") == "ofrezco" for item in saved_cards),
        "published_deck_count": published_deck_count,
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
            exact_matches = [card for card in cards if (card.get("name") or "").strip().casefold() == query.casefold()]
            if len(cards) == 1:
                notify_card_matches(cards[0], "busco")
            elif exact_matches:
                notify_card_matches(exact_matches[0], "busco")
            if len(cards) == 1:
                return redirect(url_for("card_detail", card_id=cards[0].get("id")))
            state.results = [summarize_card(card) for card in cards]
        except RuntimeError as exc:
            append_search_log(query, {})
            state.error = str(exc)

    return render_template("home_page_v2.html", state=state, search_query=state.query)


@app.route("/news", methods=["GET"])
def news_page() -> str:
    items = sorted(load_collection("news"), key=lambda item: item.get("created_at") or "", reverse=True)
    return render_template("news_page.html", news=items, search_query="")


@app.get("/news/<news_id>")
def news_detail_page(news_id: str) -> Any:
    item = next((item for item in load_collection("news") if item["news_id"] == news_id), None)
    if item is None:
        abort(404)
    return render_template("news_detail.html", item=item, content=display_content(item), search_query="")


@app.template_global()
def news_video_details(url: str) -> dict[str, str]:
    # Derive Twitch's parent from this request, never from an imported domain.
    return video_details(url, parent=urlsplit(request.host_url).hostname or "localhost")


def require_news_admin() -> None:
    # Check the stored role on every request, including after an administrator is demoted.
    user = find_user_by_email(session.get("user", {}).get("email", ""))
    if not user or normalize_role(user.get("role")) != "adm":
        abort(403)


@app.post("/news/preview")
def news_preview() -> Any:
    return news_manage_page(preview=True)


@app.post("/news/import")
def news_import() -> Any:
    require_news_admin()
    raw = request.form.get("news_json", "")
    try:
        uploaded = request.files.get("news_json_file")
        if uploaded and uploaded.filename:
            if raw.strip():
                raise ValueError("Selecciona un archivo o pega el JSON; usa solo una de las dos opciones.")
            if not uploaded.filename.lower().endswith(".json"):
                raise ValueError("Selecciona un archivo con extensión .json.")
            data = uploaded.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("El archivo JSON admite hasta 1 MB.")
            try:
                raw = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("Guarda el archivo JSON con codificación UTF-8.") from None
        editing, editor = import_news_json(raw)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("news_manage.html", news=load_collection("news"), editing=None,
                               editor=news_defaults(), import_json_input=raw, import_open=True, search_query=""), 400
    flash("JSON cargado en el editor. Revisa la vista previa y pulsa Publicar noticia cuando esté listo.", "success")
    return render_template("news_manage.html", news=load_collection("news"), editing=editing, editor=editor, search_query="")


@app.route("/news/manage", methods=["GET", "POST"])
def news_manage_page(preview: bool = False) -> Any:
    require_news_admin()
    if request.method == "POST":
        news_id = request.form.get("news_id", "").strip()
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        content = {}
        try:
            content = parse_content(request.form)
            if not title or len(title) > 200 or len(body) > 20000 or not (body or content["sections"]):
                raise ValueError("Ingresa un título de hasta 200 caracteres y un contenido o al menos una sección.")
        except ValueError as exc:
            items = sorted(load_collection("news"), key=lambda item: item.get("created_at") or "", reverse=True)
            flash(str(exc), "error")
            # Preserve the complete editor, including blocks, after a validation error.
            content = {key: request.form.get(key, "") for key in ("published_on", *NEWS_TEXT_LIMITS)}
            for key, field in (("sections", "sections_json"), ("summary_items", "summary_json")):
                try:
                    content[key] = json.loads(request.form.get(field, "[]"))
                    if not isinstance(content[key], list):
                        content[key] = []
                except ValueError:
                    content[key] = []
            return render_template("news_manage.html", news=items,
                                   editing={"news_id": news_id, "title": title, "body": body}, editor=content, search_query=""), 400
        now = datetime.now().isoformat(timespec="seconds")
        if preview:
            return render_template("news_detail.html", item={"title": title, "body": body, "created_at": now},
                                   content=content, preview=True, search_query="")
        serialized_content = json.dumps(content, ensure_ascii=False)
        with get_connection() as connection:
            if news_id:
                result = connection.execute(
                    "UPDATE news SET title = ?, body = ?, content = ?, updated_at = ? WHERE news_id = ?",
                    (title, body, serialized_content, now, news_id),
                )
                if not result.rowcount:
                    abort(404)
            else:
                connection.execute(
                    "INSERT INTO news (position, news_id, title, body, content, created_at, updated_at) "
                    "VALUES ((SELECT COALESCE(MAX(position), -1) + 1 FROM news), ?, ?, ?, ?, ?, ?)",
                    (uuid4().hex, title, body, serialized_content, now, now),
                )
        flash("Noticia publicada." if not news_id else "Noticia actualizada.", "success")
        return redirect(url_for("news_manage_page"))
    items = sorted(load_collection("news"), key=lambda item: item.get("created_at") or "", reverse=True)
    editing = None
    if request.args.get("edit"):
        editing = next((item for item in items if item["news_id"] == request.args["edit"]), None)
        if editing is None:
            abort(404)
    return render_template("news_manage.html", news=items, editing=editing,
                           editor=display_content(editing) if editing else news_defaults(), search_query="")


@app.post("/news/<news_id>/delete")
def delete_news(news_id: str) -> Any:
    require_news_admin()
    with get_connection() as connection:
        result = connection.execute("DELETE FROM news WHERE news_id = ?", (news_id,))
        if not result.rowcount:
            abort(404)
    flash("Noticia eliminada.", "success")
    return redirect(url_for("news_manage_page"))


@app.route("/community-cards", methods=["GET"])
def community_cards_page() -> str:
    status_filter = request.args.get("status", "").strip().lower()
    rarity_filter = request.args.get("rarity", "").strip().lower()
    name_filter = request.args.get("name", "").strip()
    normalized_name_filter = name_filter.lower()
    user_filter = request.args.get("user_id", "").strip().lower()
    users = get_users()
    selected_user = next((user for user in users if user.get("user_id") == user_filter), None) if user_filter else None
    if user_filter and selected_user is None:
        abort(404)
    active_cards = [(index, item) for index, item in enumerate(load_saved_cards()) if not item.get("transaction_completed", False)]
    active_owner_ids = {item.get("user_id") for _, item in active_cards if item.get("user_id")}
    legacy_owner_emails = {(item.get("user_email") or "").strip().lower() for _, item in active_cards if not item.get("user_id") and item.get("user_email")}
    available_users = [user for user in users if user.get("user_id") in active_owner_ids
                       or (user.get("email") or "").strip().lower() in legacy_owner_emails
                       or user.get("user_id") == user_filter]
    if selected_user:
        active_cards = [(index, item) for index, item in active_cards if item.get("user_id") == user_filter
                        or (not item.get("user_id") and (item.get("user_email") or "").strip().lower() == (selected_user.get("email") or "").strip().lower())]
    saved_cards = [
        build_saved_card_view(item, index, hydrate=False)
        for index, item in active_cards
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
        user_filter=user_filter,
        available_users=sorted(available_users, key=lambda user: (user.get("name") or "").casefold()),
        selected_user=selected_user,
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


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_page() -> Any:
    if request.method == "GET":
        return render_template("forgot_password_page.html", search_query="")

    email = request.form.get("email", "").strip().lower()
    requester = request.remote_addr or "unknown"
    is_rate_limited = password_reset_rate_limited(email, requester)
    user = find_user_by_email(email)

    if is_rate_limited or not user:
        flash("Si el correo existe, enviaremos instrucciones para recuperar la clave.", "success")
        return redirect(url_for("register_page"))

    token = create_password_reset_token(user)
    reset_url = url_for("reset_password_page", token=token, _external=True)
    if notify_password_reset(user, reset_url):
        flash("Enviamos un enlace para recuperar tu clave.", "success")
        return redirect(url_for("register_page"))

    flash("No se pudo enviar el correo de recuperacion. Revisa la configuracion SMTP.", "error")
    return redirect(url_for("forgot_password_page"))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_page(token: str) -> Any:
    token_index, reset_token = find_valid_password_reset_token(token)
    if reset_token is None or token_index is None:
        flash("El enlace de recuperacion no existe o ya expiro.", "error")
        return redirect(url_for("forgot_password_page"))

    if request.method == "GET":
        return render_template("reset_password_page.html", search_query="", token=token)

    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    if len(password) < 6:
        flash("La clave debe tener al menos 6 caracteres.", "error")
        return redirect(url_for("reset_password_page", token=token))
    if password != confirm_password:
        flash("Las claves no coinciden.", "error")
        return redirect(url_for("reset_password_page", token=token))

    users = get_users()
    normalized_email = (reset_token.get("user_email") or "").strip().lower()
    for user in users:
        if (user.get("email") or "").strip().lower() == normalized_email:
            user["password_hash"] = generate_password_hash(password)
            user["provider"] = user.get("provider") or "local"
            break
    else:
        flash("Usuario no encontrado.", "error")
        return redirect(url_for("forgot_password_page"))

    save_users(users)
    tokens = load_password_reset_tokens()
    tokens[token_index]["used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_password_reset_tokens(tokens)
    session.pop("user", None)
    flash("Clave actualizada correctamente. Inicia sesion con tu nueva clave.", "success")
    return redirect(url_for("register_page"))


@app.route("/saved", methods=["GET"])
def saved_cards_page() -> str:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para ver tu listado.", "error")
        return redirect(url_for("register_page"))

    current_email = current_user_identity().get("email", "")
    saved_cards = [
        {**item, "index": index} for index, item in enumerate(load_saved_cards())
        if (item.get("user_email") or "").strip().lower() == current_email
    ]
    status_filter = request.args.get("status", "").strip().lower()
    rarity_filter = request.args.get("rarity", "").strip().lower()
    available_rarities = sorted({(item.get("rarity") or "").lower() for item in saved_cards if item.get("rarity")})

    if status_filter:
        saved_cards = [item for item in saved_cards if (item.get("intent") or "").lower() == status_filter]
    if rarity_filter:
        saved_cards = [item for item in saved_cards if (item.get("rarity") or "").lower() == rarity_filter]

    page_count = max(1, (len(saved_cards) + 49) // 50)
    try:
        page = max(1, min(page_count, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    saved_cards = [build_saved_card_view(item, item["index"], hydrate=False) for item in saved_cards[(page - 1) * 50:page * 50]]

    return render_template(
        "saved_manage_grid.html",
        search_query="",
        saved_cards=saved_cards,
        status_filter=status_filter,
        rarity_filter=rarity_filter,
        available_rarities=available_rarities,
        page=page,
        page_count=page_count,
    )


@app.route("/saved/import", methods=["GET", "POST"])
def bulk_import_page() -> Any:
    if not is_authenticated_user() or not current_user_identity()["user_id"]:
        return redirect(url_for("register_page"))
    import bulk_import
    error = None
    if request.method == "POST":
        upload = request.files.get("file")
        text = request.form.get("cards_text", "").strip()
        intent = request.form.get("intent", "busco")
        try:
            if bool(upload and upload.filename) == bool(text):
                raise ValueError("Elige un archivo o pega un listado, usando solo una de las dos opciones.")
            data = upload.read(bulk_import.MAX_BYTES + 1) if upload and upload.filename else text.encode("utf-8")
            rows = bulk_import.parse_upload(data, upload.filename if upload and upload.filename else "cards.txt", intent)
            batch_id = bulk_import.create_import(current_user_identity()["user_id"], rows)
            return redirect(url_for("bulk_import_review", batch_id=batch_id))
        except ValueError as exc:
            error = str(exc)
    return render_template("bulk_import.html", error=error, search_query="")


@app.errorhandler(RequestEntityTooLarge)
def import_too_large(error: RequestEntityTooLarge) -> Any:
    if request.endpoint == "tournaments.manage":
        return "El formulario de torneos supera el límite de 2 MB.", 413
    if request.endpoint == "decks.import_cards":
        return jsonify(error="El archivo o listado supera el límite de 2 MB."), 413
    if request.endpoint == "news_import":
        flash("El archivo o texto JSON supera el límite de 1 MB.", "error")
        return render_template("news_manage.html", news=[], editing=None, editor=news_defaults(),
                               import_open=True, search_query=""), 413
    return render_template("bulk_import.html", error="El archivo o listado supera el limite de 2 MB.", search_query=""), 413


@app.route("/saved/import/<batch_id>", methods=["GET"])
def bulk_import_review(batch_id: str) -> Any:
    if not is_authenticated_user() or not current_user_identity()["user_id"]:
        return redirect(url_for("register_page"))
    from bulk_import import get_import
    batch = get_import(batch_id, current_user_identity()["user_id"])
    if not batch:
        abort(404)
    return render_template("bulk_import_review.html", batch=batch, search_query="")


@app.route("/saved/import/<batch_id>/resolve", methods=["POST"])
def bulk_import_resolve(batch_id: str) -> Any:
    if not is_authenticated_user() or not current_user_identity()["user_id"]:
        abort(401)
    from bulk_import import get_import, resolve_import
    owner_id = current_user_identity()["user_id"]
    if not get_import(batch_id, owner_id):
        abort(404)
    try:
        batch = resolve_import(batch_id, owner_id)
        return jsonify({"rows": batch["rows"], "pending": sum(row["status"] == "pending" for row in batch["rows"])})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/saved/import/<batch_id>/confirm", methods=["POST"])
def bulk_import_confirm(batch_id: str) -> Any:
    if not is_authenticated_user() or not current_user_identity()["user_id"]:
        abort(401)
    from bulk_import import confirm_import, get_import
    owner = current_user_identity()
    if not get_import(batch_id, owner["user_id"]):
        abort(404)
    try:
        selected = {int(index) for index in request.form.getlist("row")}
        added = confirm_import(batch_id, owner, selected)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("bulk_import_review", batch_id=batch_id))
    users, saved_cards, notices = load_users(), load_saved_cards(), []
    for item in added:
        notify_card_matches({"id": item["card_id"], "name": item["name"]}, item["intent"], owner,
                            users=users, saved_cards=saved_cards, notification_sink=notices)
    if notices:
        add_card_notifications(notices)
    flash(f"Carga terminada: {len(added)} cartas agregadas. Las coincidencias quedan para el proximo resumen de correo.", "success")
    return redirect(url_for("saved_cards_page"))


@app.route("/api/card/<card_id>/editions", methods=["GET"])
def card_editions_api(card_id: str) -> Any:
    if not is_authenticated_user():
        abort(401)
    try:
        return jsonify({"editions": get_card_editions(get_card(card_id), card_id)})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502


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


@app.route("/internal/email-digest", methods=["POST"])
def email_digest_trigger() -> Any:
    expected = os.environ.get("DIGEST_SCHEDULER_TOKEN", "")
    if not expected:
        return jsonify({"error": "Scheduler not configured"}), 503
    authorization = request.headers.get("Authorization", "")
    if not secrets.compare_digest(authorization.encode("utf-8"), f"Bearer {expected}".encode("utf-8")):
        abort(403)
    from email_digest import send_due_digests
    try:
        report = send_due_digests()
        return jsonify(report), 503 if report["failed"] else 200
    except ValueError:
        app.logger.exception("No se pudo ejecutar el resumen de correos.")
        return jsonify({"error": "Email configuration error"}), 503


@app.route("/notifications", methods=["GET"])
def notifications_page() -> Any:
    if not is_authenticated_user():
        flash("Debes iniciar sesion para ver tus notificaciones.", "error")
        return redirect(url_for("register_page"))
    user_id = current_user_identity()["user_id"]
    notifications = load_card_notifications(user_id)
    mark_card_notifications_read(user_id)
    return render_template("notifications_page.html", notifications=notifications, search_query="")


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
            "email_state": "pending",
        }
    )
    save_messages(messages)
    flash("Mensaje enviado. El aviso por correo se incluira en el proximo resumen de las 09:30 o 16:30 (Chile).", "success")
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
            "email_state": "pending",
        }
    )
    save_messages(messages)
    flash("Mensaje enviado. El aviso por correo se incluira en el proximo resumen de las 09:30 o 16:30 (Chile).", "success")
    return redirect(url_for("messages_page"))


SEARCH_HISTORY_PAGE_SIZE = 10


@app.route("/search-history", methods=["GET"])
def search_history_page() -> Any:
    if not is_admin_user():
        flash("Solo el perfil adm puede acceder al historial de busquedas.", "error")
        return redirect(url_for("index"))

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
    if not is_admin_user():
        flash("Solo el perfil adm puede acceder al dashboard de busquedas.", "error")
        return redirect(url_for("index"))

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
        if request.args.get("from_search") == "1":
            notify_card_matches(selected_card_raw, "busco")
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


@app.route("/card/<card_id>/recommendations", methods=["GET"])
def card_recommendations(card_id: str) -> Any:
    try:
        similar_cards = get_similar_cards(get_card(card_id))
        return render_template("similar_cards.html", similar_cards=similar_cards)
    except RuntimeError:
        app.logger.warning("Could not load recommendations for card %s", card_id)
        return render_template("similar_cards.html", similar_cards=[], unavailable=True), 502


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
                "image_url": first_available_image(selected_card_raw),
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
        notify_card_matches(selected_card_raw, intent, owner)
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
        quantity = max(1, min(9999, int(quantity_raw)))
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
        "image_url": first_available_image(selected_card_raw),
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
    if (not saved_cards[item_index]["transaction_completed"]
            and ((current_item.get("name") or "").casefold(), current_item.get("intent"))
            != ((selected_card_raw.get("name") or "").casefold(), intent)):
        notify_card_matches(selected_card_raw, intent, owner)
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
    if not current_item["transaction_completed"]:
        notify_card_matches(current_item, current_item.get("intent", ""), {
            "user_id": current_item.get("user_id", ""),
            "name": current_item.get("user_name", ""),
            "email": current_item.get("user_email", ""),
        })
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


from decks import register_deck_routes
register_deck_routes(app, current_user_identity)
from tournaments import register_tournament_routes
register_tournament_routes(app, require_news_admin)


if __name__ == "__main__":
    app.run(debug=True)
