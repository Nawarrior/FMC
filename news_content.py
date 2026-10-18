"""Structured news fields shared by the editor, preview and public article."""
import json
import re
from datetime import date, datetime
from urllib.parse import urlsplit, parse_qs, urlencode
from zoneinfo import ZoneInfo


TEXT_LIMITS = {
    "category": 80, "lead": 1200, "author": 120, "reading_time": 80,
    "hero_url": 2000, "hero_alt": 300, "hero_caption": 1000, "hero_source_url": 2000,
    "editorial_note": 2000, "summary_title": 160, "summary_note": 2000,
    "button_label": 120, "button_url": 2000, "source_label": 200,
    "source_url": 2000, "source_note": 2000, "rights": 6000,
    "cta_title": 200, "cta_text": 1000, "cta_label": 120, "cta_url": 2000,
}
BLOCK_TYPES = {"text", "image", "gallery", "highlights", "links", "list", "video"}


def youtube_embed_url(value):
    checked_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname
    parts = parsed.path.strip("/").split("/")
    if host == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        if parsed.path == "/watch" and host not in {"youtube-nocookie.com", "www.youtube-nocookie.com"}:
            ids = parse_qs(parsed.query).get("v", [])
            video_id = ids[0] if len(ids) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"embed", "shorts"}:
            video_id = parts[1]
        else:
            video_id = ""
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Ingresa un enlace válido a un video de YouTube.")
    return f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0"


def video_details(value, parent="localhost"):
    checked_url(value)
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").split("/")
    host = parsed.hostname
    query = parse_qs(parsed.query)
    clip = ""
    vod = ""
    if host == "clips.twitch.tv":
        if len(parts) == 1 and parts[0] != "embed":
            clip = parts[0]
        elif parsed.path == "/embed" and len(query.get("clip", [])) == 1:
            clip = query["clip"][0]
    elif host in {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
        if len(parts) == 3 and parts[1] == "clip":
            clip = parts[2]
        elif len(parts) == 2 and parts[0] == "videos":
            vod = parts[1]
    elif host == "player.twitch.tv" and len(query.get("video", [])) == 1:
        vod = query["video"][0].removeprefix("v")
    else:
        embed = youtube_embed_url(value)
        return {"provider": "youtube", "video_id": embed.split("/embed/", 1)[1].split("?", 1)[0], "embed_url": embed}
    if clip and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", clip):
        embed = "https://clips.twitch.tv/embed?" + urlencode({"clip": clip, "parent": parent, "autoplay": "false"})
        return {"provider": "twitch", "video_id": clip, "embed_url": embed}
    if vod and re.fullmatch(r"[0-9]{1,25}", vod):
        embed = "https://player.twitch.tv/?" + urlencode({"video": "v" + vod, "parent": parent, "autoplay": "false"})
        return {"provider": "twitch", "video_id": vod, "embed_url": embed}
    raise ValueError("Ingresa un enlace válido a un clip o video grabado de Twitch.")


def defaults():
    return {"author": "Redacción FMC", "published_on": datetime.now(ZoneInfo("America/Santiago")).date().isoformat(),
            "sections": [], "summary_items": []}


def checked_text(value, limit, label):
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"Revisa {label}: supera el largo permitido o tiene un formato inválido.")
    return value.strip()


def checked_url(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if not valid or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Las imágenes y enlaces deben usar una dirección http:// o https:// válida.")
    return value


def read_list(form, name, maximum):
    raw = form.get(name, "[]")
    if len(raw) > 250000:
        raise ValueError("El contenido de la noticia es demasiado extenso.")
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("No se pudieron leer los bloques de la noticia.") from None
    if not isinstance(items, list) or len(items) > maximum or any(not isinstance(item, dict) for item in items):
        raise ValueError("La cantidad o el formato de los bloques no es válido.")
    return items


def parse_content(form):
    content = {}
    for key, limit in TEXT_LIMITS.items():
        value = checked_text(form.get(key, ""), limit, key)
        content[key] = checked_url(value) if key.endswith("_url") else value
    content["author"] = content["author"] or "Redacción FMC"
    published_on = form.get("published_on") or defaults()["published_on"]
    try:
        content["published_on"] = date.fromisoformat(published_on).isoformat()
    except ValueError:
        raise ValueError("Ingresa una fecha de publicación válida.") from None
    if content["hero_url"] and not content["hero_alt"]:
        raise ValueError("Agrega una descripción a la imagen principal.")
    content["summary_items"] = []
    for item in read_list(form, "summary_json", 12):
        label = checked_text(item.get("label", ""), 120, "la etiqueta del dato esencial")
        value = checked_text(item.get("value", ""), 400, "el dato esencial")
        if label or value:
            if not label or not value:
                raise ValueError("Cada dato esencial necesita una etiqueta y un valor.")
            content["summary_items"].append({"label": label, "value": value})
    content["sections"] = []
    for section in read_list(form, "sections_json", 30):
        kind = section.get("type", "text")
        if not isinstance(kind, str) or kind not in BLOCK_TYPES:
            raise ValueError("El tipo de sección no es válido.")
        block = {"type": kind, "title": checked_text(section.get("title", ""), 200, "el título de sección"),
                 "text": checked_text(section.get("text", ""), 20000, "el texto de sección"), "items": []}
        items = section.get("items", [])
        if not isinstance(items, list) or len(items) > 24 or any(not isinstance(item, dict) for item in items):
            raise ValueError("Cada sección admite hasta 24 elementos.")
        for item in items:
            cleaned = {key: checked_text(item.get(key, ""), limit, "el elemento de sección") for key, limit in
                       (("title", 200), ("text", 2000), ("image_url", 2000), ("alt", 300), ("credit", 1000), ("url", 2000))}
            cleaned["image_url"] = checked_url(cleaned["image_url"])
            cleaned["url"] = checked_url(cleaned["url"])
            if not any(cleaned.values()):
                continue
            if kind in {"image", "gallery"} and (not cleaned["image_url"] or not cleaned["alt"]):
                raise ValueError("Cada imagen necesita una dirección y una descripción.")
            if kind == "links" and (not cleaned["url"] or not cleaned["title"]):
                raise ValueError("Cada enlace necesita un título y una dirección.")
            if kind == "video":
                if not cleaned["title"]:
                    raise ValueError("Cada video necesita un título.")
                cleaned.update(video_details(cleaned["url"]))
            block["items"].append(cleaned)
        if kind != "text" and not block["items"]:
            raise ValueError("Agrega al menos un elemento a la sección o elimínala.")
        if kind == "image" and len(block["items"]) > 1:
            raise ValueError("Usa una galería para incluir varias imágenes.")
        if block["title"] or block["text"] or block["items"]:
            content["sections"].append(block)
    return content


def display_content(item):
    value = item.get("content")
    return value if isinstance(value, dict) else {}


def import_json(raw):
    """Validate a single article and return editor fields without persisting it."""
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("El JSON admite hasta 1 MB.")
    try:
        payload = json.loads(raw.lstrip("\ufeff"))
    except (ValueError, RecursionError):
        raise ValueError("El JSON no es válido. Revisa las comillas, comas y llaves.") from None
    if not isinstance(payload, dict):
        raise ValueError("El JSON debe contener una sola noticia como objeto.")
    if payload.get("type") == "news_article" or "schema_version" in payload:
        from news_article_import import convert_article
        payload = convert_article(payload)
    allowed_content = {*TEXT_LIMITS, "published_on", "sections", "summary_items"}
    allowed_root = {"title", "body", "content"} if "content" in payload else {"title", "body", *allowed_content}
    if set(payload) - allowed_root:
        raise ValueError("El JSON contiene campos desconocidos. Usa la plantilla de ejemplo.")
    source = payload.get("content", payload)
    if not isinstance(source, dict) or ("content" in payload and set(source) - allowed_content):
        raise ValueError("El campo content debe contener los campos de la plantilla de ejemplo.")
    title = checked_text(payload.get("title", ""), 200, "el título")
    body = checked_text(payload.get("body", ""), 20000, "la introducción")
    fields = {key: source.get(key, "") for key in TEXT_LIMITS}
    published_on = source.get("published_on", defaults()["published_on"])
    if not isinstance(published_on, str):
        raise ValueError("La fecha published_on debe ser un texto con formato AAAA-MM-DD.")
    fields["published_on"] = published_on
    fields["sections_json"] = json.dumps(source.get("sections", []), ensure_ascii=False)
    fields["summary_json"] = json.dumps(source.get("summary_items", []), ensure_ascii=False)
    content = parse_content(fields)
    if not title or not (body or content["sections"]):
        raise ValueError("La noticia necesita un título y una introducción o al menos una sección.")
    return {"news_id": "", "title": title, "body": body}, content
