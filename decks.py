"""Owned deck management and read-only community decks."""
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for, Response

import bulk_import
import database
from deck_insights import market_data, analyze

BOARDS = {"main": "Principal", "commander": "Comandante", "side": "Sideboard", "maybe": "Considerando"}
FORMATS = ("Commander", "Standard", "Pioneer", "Modern", "Pauper", "Legacy", "Vintage", "Brawl", "Historic", "Timeless", "Casual")
HEADERS = {"deck": "main", "mainboard": "main", "main": "main", "principal": "main",
           "commander": "commander", "commanders": "commander", "comandante": "commander",
           "sideboard": "side", "side": "side", "maybeboard": "maybe", "considering": "maybe", "considerando": "maybe"}


class Conflict(ValueError):
    pass


def get_deck(deck_id, owner):
    with database.get_connection() as db:
        row = db.execute("SELECT * FROM decks WHERE deck_id = ? AND user_id = ?", (deck_id, owner)).fetchone()
    return database._row_to_item("decks", row) if row else None


def list_decks(owner):
    with database.get_connection() as db:
        rows = db.execute("SELECT * FROM decks WHERE user_id = ? ORDER BY updated_at DESC, id DESC", (owner,)).fetchall()
    return [database._row_to_item("decks", row) for row in rows]


def public_view(row):
    stored = database._row_to_item("decks", row)
    return {"deck_id": stored["deck_id"], "name": stored["name"], "description": stored["description"],
            "format": stored["format"], "author_name": row["author_name"] or "Usuario",
            "author_profile_url": url_for("user_profile_page", user_id=stored["user_id"]) if row["author_name"] else "",
            "published": True, "cards": [{key: entry[key] for key in ("row_id", "name", "quantity", "board", "status", "card") if key in entry}
                for entry in stored["cards"]]}


def metadata(form):
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    format_name = form.get("format", "Casual")
    if not name or len(name) > 120 or len(description) > 5000 or format_name not in FORMATS:
        raise ValueError("Indica un nombre de hasta 120 caracteres, un formato y una descripción de hasta 5.000 caracteres.")
    return {"name": name, "description": description, "format": format_name}


def create_deck(owner, fields):
    deck_id = uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    with database.get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM decks WHERE user_id = ?", (owner,)).fetchone()[0] >= 100:
            raise ValueError("Puedes mantener hasta 100 decks.")
        db.execute("INSERT INTO decks (position, deck_id, user_id, name, format, description, cards, revision, published, created_at, updated_at) VALUES (0, ?, ?, ?, ?, ?, '[]', 0, 0, ?, ?)",
                   (deck_id, owner, fields["name"], fields["format"], fields["description"], now, now))
    return deck_id


def save_deck(deck, expected):
    with database.get_connection() as db:
        result = db.execute("UPDATE decks SET name = ?, format = ?, description = ?, cards = ?, published = ?, revision = revision + 1, updated_at = ? WHERE deck_id = ? AND user_id = ? AND revision = ?",
            (deck["name"], deck["format"], deck["description"], json.dumps(deck["cards"], ensure_ascii=False), int(bool(deck.get("published"))), datetime.now().isoformat(timespec="seconds"), deck["deck_id"], deck["user_id"], expected))
        if not result.rowcount:
            raise Conflict("El deck cambió en otra pestaña. Se cargó la versión actual; vuelve a intentar la acción.")
    deck["revision"] = expected + 1
    return deck


def parse_list(data, filename, board):
    if board not in BOARDS:
        raise ValueError("Selecciona una zona válida del deck.")
    if len(data) > bulk_import.MAX_BYTES:
        raise ValueError("El archivo no debe superar 2 MB.")
    suffix = Path(filename).suffix.lower()
    rows = []
    if suffix == ".txt":
        try:
            text = data.decode("utf-16") if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        for line_number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", "//")):
                continue
            header = re.sub(r"\s*\(\d+\)\s*$", "", line.strip("[]:" )).lower()
            if header in HEADERS:
                board = HEADERS[header]
                continue
            row_board = "commander" if re.search(r"\s+\*CMDR\*", line, flags=re.I) else board
            line = re.sub(r"\s+\*(?:CMDR|F|E)\*", "", line, flags=re.I)
            if line.upper().startswith("SB:"):
                row_board, line = "side", line[3:].strip()
            parsed = bulk_import.parse_upload(line.encode(), "deck.txt", "busco")[0]
            rows.append({**parsed, "line": line_number, "board": row_board})
            if len(rows) > 500:
                raise ValueError("El deck admite hasta 500 filas.")
    else:
        rows = [{**row, "board": board} for row in bulk_import.parse_upload(data, filename, "busco")]
    if not rows:
        raise ValueError("El listado está vacío.")
    return [{**row, "row_id": uuid4().hex} for row in rows]


def card_data(card):
    face = (card.get("card_faces") or [{}])[0]
    images = card.get("image_uris") or face.get("image_uris") or {}
    return {"id": card["id"], "name": card["name"], "set": card.get("set", ""),
            "number": card.get("collector_number", ""), "image_url": images.get("normal", ""),
            "art_url": images.get("art_crop", ""), "type_line": card.get("type_line") or face.get("type_line", ""),
            "mana_cost": card.get("mana_cost") or face.get("mana_cost", ""), "cmc": card.get("cmc", 0),
            "colors": card.get("colors") or face.get("colors", []), "oracle_text": card.get("oracle_text") or face.get("oracle_text", ""),
            "rarity": card.get("rarity", ""), **market_data(card)}


def resolve_rows(rows):
    deck = {"cards": rows}
    pending = [row for row in deck["cards"] if row["status"] == "pending"][:25]
    if not pending:
        return rows
    identifiers = []
    for row in pending:
        identifier = {"name": row["name"]}
        if row.get("set"):
            identifier["set"] = row["set"]
        if row.get("number"):
            identifier = {"set": row["set"], "collector_number": row["number"]}
        if identifier not in identifiers:
            identifiers.append(identifier)
    cards = bulk_import.fetch_collection(identifiers)
    matches = {row["row_id"]: next((card for card in cards if bulk_import.card_matches(row, card)), None) for row in pending}
    fallback = [row for row in pending if matches[row["row_id"]] is None and (row.get("set") or row.get("number"))]
    if fallback:
        # Arena exports can use umbrella set codes (Y22/Y25) rather than Scryfall set codes.
        # Deck membership depends on the exact name; preserve the card, choose an available printing.
        by_name = []
        for row in fallback:
            identifier = {"name": row["name"]}
            if identifier not in by_name:
                by_name.append(identifier)
        fallback_cards = bulk_import.fetch_collection(by_name)
        for row in fallback:
            name_only = {**row, "set": "", "number": ""}
            matches[row["row_id"]] = next((card for card in fallback_cards if bulk_import.card_matches(name_only, card)), None)
    for row in pending:
        matched = matches[row["row_id"]]
        if matched:
            if row in fallback:
                row["edition_note"] = f"Edición ajustada: {row.get('set', '').upper()} #{row.get('number', '')} → {matched.get('set', '').upper()} #{matched.get('collector_number', '')}."
            row.update(status="ready", error="", card=card_data(matched), name=matched["name"])
            row.update(set=matched.get("set", ""), number=matched.get("collector_number", ""))
        else:
            row.update(status="error", error="No encontrada. Corrige el nombre o elimina esta fila.")
    # Collapse repeated printings in the same board while preserving quantities.
    merged = []
    seen = {}
    for row in deck["cards"]:
        key = (row.get("card", {}).get("id"), row["board"])
        if row["status"] == "ready" and key in seen and seen[key]["quantity"] + row["quantity"] <= 9999:
            seen[key]["quantity"] += row["quantity"]
        else:
            merged.append(row)
            if row["status"] == "ready":
                seen[key] = row
    return merged


def resolve_deck(deck):
    if not any(row["status"] == "pending" for row in deck["cards"]):
        return deck
    deck["cards"] = resolve_rows(deck["cards"])
    return save_deck(deck, deck["revision"])


def register_deck_routes(app, identity):
    routes = Blueprint("decks", __name__, url_prefix="/decks")

    def owner():
        user = identity()
        if not user.get("user_id"):
            abort(401)
        return user["user_id"]

    def owned(deck_id):
        deck = get_deck(deck_id, owner())
        if not deck:
            abort(404)
        return deck

    @routes.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 409 if isinstance(error, Conflict) else 400

    @routes.get("")
    def index():
        if not identity().get("user_id"):
            flash("Inicia sesión para crear y ver tus decks.", "error")
            return redirect(url_for("register_page"))
        return render_template("decks.html", decks=list_decks(owner()), formats=FORMATS, search_query="")

    @routes.get("/public")
    def public_index():
        format_name = request.args.get("format", "")
        user_filter = request.args.get("user_id", "").strip().lower()
        if format_name and format_name not in FORMATS:
            abort(400)
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        where = "d.published = 1"
        params = []
        if format_name:
            where += " AND d.format = ?"
            params.append(format_name)
        if user_filter:
            where += " AND d.user_id = ?"
            params.append(user_filter)
        with database.get_connection() as db:
            authors = db.execute("SELECT DISTINCT d.user_id, COALESCE((SELECT name FROM users WHERE user_id = d.user_id LIMIT 1), 'Usuario') AS name FROM decks d WHERE d.published = 1 ORDER BY name COLLATE NOCASE").fetchall()
            total = db.execute("SELECT COUNT(*) FROM decks d WHERE " + where, params).fetchone()[0]
            pages = max(1, (total + 23) // 24)
            page = min(page, pages)
            rows = db.execute("SELECT d.*, (SELECT name FROM users WHERE user_id = d.user_id LIMIT 1) AS author_name FROM decks d WHERE " + where + " ORDER BY d.updated_at DESC, d.id DESC LIMIT 24 OFFSET ?", (*params, (page - 1) * 24)).fetchall()
        return render_template("public_decks.html", decks=[public_view(row) for row in rows], formats=FORMATS,
                               format_filter=format_name, user_filter=user_filter, authors=authors,
                               total=total, page=page, pages=pages, search_query="")

    @routes.get("/public/<deck_id>")
    def public_detail(deck_id):
        with database.get_connection() as db:
            row = db.execute("SELECT d.*, (SELECT name FROM users WHERE user_id = d.user_id LIMIT 1) AS author_name FROM decks d WHERE deck_id = ? AND published = 1", (deck_id,)).fetchone()
        if not row:
            abort(404)
        return render_template("deck_detail.html", deck=public_view(row), boards=BOARDS, readonly=True, search_query="")

    @routes.get("/public/<deck_id>/analysis")
    def public_analysis(deck_id):
        with database.get_connection() as db:
            row = db.execute("SELECT * FROM decks WHERE deck_id = ? AND published = 1", (deck_id,)).fetchone()
        if not row:
            abort(404)
        return analysis_response(database._row_to_item("decks", row))

    def analysis_response(deck):
        try:
            response = jsonify(analyze(deck))
            response.headers["Cache-Control"] = "no-store"
            return response
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 502

    @routes.get("/<deck_id>/analysis")
    def analysis(deck_id):
        return analysis_response(owned(deck_id))

    @routes.post("")
    def create():
        user_id = owner()
        try:
            deck_id = create_deck(user_id, metadata(request.form))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("decks.index"))
        return redirect(url_for("decks.detail", deck_id=deck_id))

    @routes.get("/<deck_id>")
    def detail(deck_id):
        return render_template("deck_detail.html", deck=owned(deck_id), formats=FORMATS, boards=BOARDS, search_query="")

    @routes.get("/<deck_id>/data")
    def data(deck_id):
        return jsonify(owned(deck_id))

    def expected_revision(deck):
        try:
            revision = int(request.form.get("revision", ""))
        except ValueError:
            raise ValueError("No se pudo leer la versión del deck. Actualiza la página.") from None
        if revision != deck["revision"]:
            raise Conflict("El deck cambió en otra pestaña. Se cargó la versión actual; vuelve a intentar la acción.")
        return revision

    @routes.post("/<deck_id>/import")
    def import_cards(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        mode = request.form.get("mode", "replace")
        if mode not in {"replace", "append"}:
            raise ValueError("El modo de carga no es válido.")
        upload = request.files.get("file")
        text = request.form.get("cards_text", "").strip()
        if bool(upload and upload.filename) == bool(text):
            raise ValueError("Elige un archivo o pega un listado, usando solo una opción.")
        rows = parse_list(upload.read(bulk_import.MAX_BYTES + 1) if upload and upload.filename else text.encode(),
                          upload.filename if upload and upload.filename else "deck.txt", request.form.get("board", "main"))
        invalid_rows = [row for row in rows if row["status"] == "error"]
        if invalid_rows:
            first = invalid_rows[0]
            raise ValueError(f"Revisa la fila {first['line']}: {first['error']} El listado actual se conserva.")
        if (len(deck["cards"]) if mode == "append" else 0) + len(rows) > 500:
            raise ValueError("El deck admite hasta 500 filas. Elimina filas antes de agregar más.")
        deck["cards"] = deck["cards"] + rows if mode == "append" else rows
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/clear")
    def clear_cards(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        deck["cards"] = []
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/resolve")
    def resolve(deck_id):
        deck = owned(deck_id)
        expected_revision(deck)
        try:
            return jsonify(resolve_deck(deck))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 502

    @routes.post("/<deck_id>/edit")
    def edit(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        deck.update(metadata(request.form))
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/publish")
    def publish(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        value = request.form.get("published")
        if value not in {"0", "1"}:
            raise ValueError("Selecciona Publicar u Ocultar el deck.")
        deck["published"] = value == "1"
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/retry")
    def retry_missing(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        for row in deck["cards"]:
            if row["status"] == "error" and row.get("error", "").startswith("No encontrada."):
                row.update(status="pending", error="")
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/cards/<row_id>")
    def edit_card(deck_id, row_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        row = next((row for row in deck["cards"] if row["row_id"] == row_id), None)
        if row is None:
            abort(404)
        if request.form.get("action") == "delete":
            deck["cards"].remove(row)
        else:
            try:
                quantity = int(request.form.get("quantity", row["quantity"]))
            except ValueError:
                raise ValueError("La cantidad debe ser un entero entre 1 y 9999.") from None
            board = request.form.get("board", row["board"])
            if not 1 <= quantity <= 9999 or board not in BOARDS:
                raise ValueError("Indica una cantidad entre 1 y 9999 y una zona válida.")
            row.update(quantity=quantity, board=board)
            if row["status"] == "error" and "quantity" in request.form:
                row.update(status="pending", error="")
            if "name" in request.form:
                name = request.form["name"].strip()
                if not name or len(name) > 200:
                    raise ValueError("Indica un nombre de carta de hasta 200 caracteres.")
                row.update(name=name, status="pending", error="", set="", number="")
                row.pop("card", None)
                row.pop("edition_note", None)
        return jsonify(save_deck(deck, revision))

    @routes.post("/<deck_id>/delete")
    def delete(deck_id):
        deck = owned(deck_id)
        revision = expected_revision(deck)
        with database.get_connection() as db:
            result = db.execute("DELETE FROM decks WHERE deck_id = ? AND user_id = ? AND revision = ?", (deck_id, deck["user_id"], revision))
            if not result.rowcount:
                raise Conflict("El deck cambió. Actualiza antes de eliminarlo.")
        return jsonify(redirect=url_for("decks.index"))

    @routes.get("/<deck_id>/export")
    def export(deck_id):
        deck = owned(deck_id)
        lines = []
        for board, label in BOARDS.items():
            rows = [row for row in deck["cards"] if row["board"] == board]
            if rows:
                lines.extend([label, *[f"{row['quantity']} {row['name']}" + (f" ({row['card']['set']}) {row['card']['number']}" if row.get("card", {}).get("set") else "") for row in rows], ""])
        return Response("\n".join(lines), mimetype="text/plain", headers={"Content-Disposition": 'attachment; filename="deck.txt"'})

    app.register_blueprint(routes)
