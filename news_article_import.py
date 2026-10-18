"""Convert the editorial news_article 1.0 format into the news editor format."""
from datetime import date
from decimal import Decimal, InvalidOperation

from news_content import checked_text, checked_url, video_details


def text(value, limit=2000):
    return checked_text("" if value is None else value, limit, "el formato editorial")


def object_value(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} debe ser un objeto JSON.")
    return value


def list_value(value, name, limit=30):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} debe ser una lista de hasta {limit} elementos.")
    return value


def iso_date(value):
    value = text(value, 40)
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("Las fechas editoriales deben usar el formato AAAA-MM-DD.") from None


def convert_article(payload):
    if payload.get("schema_version") != "1.0" or payload.get("type") != "news_article":
        raise ValueError("El formato editorial debe usar schema_version 1.0 y type news_article.")
    sources = list_value(payload.get("sources", []), "sources", 24)
    source_map = {}
    source_links = []
    for source in sources:
        source = object_value(source, "Cada fuente")
        key = text(source.get("id"), 120)
        if not key or key in source_map:
            raise ValueError("Cada fuente necesita un id único.")
        url = checked_url(text(source.get("url")))
        if not url:
            raise ValueError("Cada fuente necesita una dirección URL.")
        notes = [text(source.get("publisher"), 200)]
        for field, label in (("publication_date", "Publicada"), ("accessed_date", "Consultada")):
            value = iso_date(source.get(field))
            if value:
                notes.append(f"{label}: {value}")
        item = {"title": text(source.get("title"), 200) or notes[0] or "Fuente original",
                "url": url, "text": " · ".join(filter(None, notes))}
        source_map[key] = item
        source_links.append(item)

    def source_for(value):
        key = text(value, 120)
        if key and key not in source_map:
            raise ValueError(f"La fuente {key} no está definida en sources.")
        return source_map.get(key, {})

    def image(item):
        item = object_value(item, "Cada imagen")
        source_for(item.get("source_id"))
        return {"title": text(item.get("name") or item.get("title"), 200),
                "image_url": text(item.get("url")), "alt": text(item.get("alt"), 300),
                "text": text(item.get("caption")),
                "credit": "\n".join(filter(None, (text(item.get("credit"), 1000), text(item.get("rights_notice"), 1000))))}

    banner = object_value(payload.get("banner") or {}, "banner")
    banner_source = source_for(banner.get("source_id"))
    banner_credit = "\n".join(filter(None, (text(banner.get("caption"), 1000), text(banner.get("credit"), 1000), text(banner.get("rights_notice"), 1000))))
    content = {"lead": text(payload.get("summary"), 1200), "category": text(payload.get("category"), 80),
               "author": text(payload.get("author"), 120), "editorial_note": text(payload.get("date_note")),
               "hero_url": text(banner.get("url")), "hero_alt": text(banner.get("alt"), 300),
               "hero_caption": banner_credit, "hero_source_url": banner_source.get("url", ""),
               "sections": [], "summary_items": []}
    if payload.get("editorial_date"):
        content["published_on"] = iso_date(payload["editorial_date"])
    if source_links:
        content.update(source_label=source_links[0]["title"], source_url=source_links[0]["url"], source_note=source_links[0]["text"])
    for section in list_value(payload.get("content", []), "content"):
        section = object_value(section, "Cada sección")
        if section.get("type") != "section":
            raise ValueError("Los elementos de content deben tener type section.")
        heading = text(section.get("heading"), 200)
        blocks = list_value(section.get("blocks", []), "blocks")
        if not blocks:
            raise ValueError("Cada sección editorial necesita al menos un bloque.")
        for index, block in enumerate(blocks):
            block = object_value(block, "Cada bloque")
            source_for(block.get("source_id"))
            kind = block.get("type")
            converted = {"title": heading if index == 0 else "", "text": "", "items": []}
            if kind == "paragraph":
                converted.update(type="text", text=text(block.get("text"), 20000))
            elif kind == "image":
                converted.update(type="image", items=[image(block)])
            elif kind == "gallery":
                converted.update(type="gallery", items=[image(item) for item in list_value(block.get("items", []), "gallery.items", 24)])
            elif kind == "list":
                converted.update(type="list", items=[{"title": "", "text": text(item)} if isinstance(item, str) else
                    {"title": text(object_value(item, "Cada elemento de lista").get("title") or item.get("name"), 200),
                     "text": text(item.get("text"))} for item in list_value(block.get("items", []), "list.items", 24)])
            elif kind == "links":
                entries = []
                for entry in list_value(block.get("items", []), "links.items", 24):
                    entry = object_value(entry, "Cada enlace")
                    entries.append({"title": text(entry.get("label") or entry.get("title"), 200),
                                    "url": text(entry.get("url")), "text": text(entry.get("text"))})
                converted.update(type="links", items=entries)
            else:
                raise ValueError("Bloque editorial no admitido. Usa paragraph, list, image, gallery o links.")
            content["sections"].append(converted)
    videos = list_value(payload.get("videos") or [], "videos", 12)
    if videos:
        entries = []
        for video in videos:
            video = object_value(video, "Cada video")
            if not isinstance(video.get("provider"), str) or video["provider"] not in {"youtube", "twitch"}:
                raise ValueError("Solo se admiten videos de YouTube y Twitch.")
            source_for(video.get("source_id"))
            url = text(video.get("url"))
            details = video_details(url)
            if details["provider"] != video["provider"]:
                raise ValueError("El proveedor no coincide con el enlace del video.")
            if video.get("video_id") and video.get("video_id") != details["video_id"]:
                raise ValueError("El video_id no coincide con el enlace del video.")
            if video.get("embed_url") and video_details(text(video["embed_url"])) != details:
                raise ValueError("El enlace de reproducción no coincide con el video.")
            channel = text(video.get("channel_name"), 200)
            if video.get("channel_url"):
                channel = " · ".join(filter(None, (channel, checked_url(text(video["channel_url"])))))
            credits = "\n".join(filter(None, (text(video.get("credit"), 1000), text(video.get("rights_notice"), 1000), channel)))
            entries.append({"title": text(video.get("title"), 200), "url": url, "credit": credits})
        content["sections"].append({"type": "video", "title": "Video", "text": text(payload.get("video_note")), "items": entries})
    elif payload.get("video_note"):
        content["sections"].append({"type": "text", "title": "Videos", "text": text(payload["video_note"])})
    if source_links:
        content["sections"].append({"type": "links", "title": "Fuentes consultadas", "items": source_links})
    facts = object_value(payload.get("key_facts") or {}, "key_facts")
    source_for(facts.get("source_id"))
    for key, label in (("series", "Serie"), ("platform", "Plataforma"), ("format", "Formato"), ("set", "Colección"), ("featured_match", "Enfrentamiento")):
        if facts.get(key):
            content["summary_items"].append({"label": label, "value": text(facts[key], 400)})
    release = iso_date(facts.get("release_date"))
    if release:
        content["summary_items"].append({"label": "Fecha anunciada", "value": release})
    price = facts.get("announced_price")
    if price is not None:
        price = object_value(price, "announced_price")
        try:
            amount = Decimal(str(price.get("amount")))
            if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000"):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise ValueError("El precio anunciado debe ser un número válido.") from None
        currency = text(price.get("currency"), 10)
        formatted = format(amount, ".2f").replace(".", ",")
        content["summary_items"].append({"label": "Precio oficial", "value": ("US$" if currency == "USD" else currency + " ") + formatted})
    limit = facts.get("purchase_limit_per_customer")
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("El límite de compra debe ser un entero positivo.")
        content["summary_items"].append({"label": "Límite", "value": f"{limit} unidades por cliente"})
    if facts.get("availability"):
        content["summary_items"].append({"label": "Disponibilidad", "value": text(facts["availability"], 400)})
    content.update(summary_title="Lo esencial", summary_note=text(facts.get("price_note")),
                   button_label="Visitar tienda", button_url=text(facts.get("purchase_url")))
    rights = object_value(payload.get("rights") or {}, "rights")
    content["rights"] = "\n\n".join(filter(None, (text(rights.get(key), 6000) for key in
        ("editorial_credit", "images_credit", "copyright_notice", "license", "attribution_note", "independence_notice"))))
    cta = object_value(payload.get("call_to_action") or {}, "call_to_action")
    content.update(cta_title=text(cta.get("title"), 200), cta_text=text(cta.get("text"), 1000),
                   cta_label=text(cta.get("label"), 120), cta_url=text(cta.get("url")))
    return {"title": payload.get("title", ""), "body": "", "content": content}
