"""Public tournament records with admin-only maintenance and optional associations."""
from datetime import date, datetime
import json
from uuid import uuid4

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import database
from decks import FORMATS, BOARDS, parse_list, resolve_rows
from deck_insights import analyze

EVENT_FORMATS = (*FORMATS, "Mixto")


def get_event(event_id):
    with database.get_connection() as db:
        row = db.execute("SELECT * FROM tournaments WHERE tournament_id = ?", (event_id,)).fetchone()
    return database._row_to_item("tournaments", row) if row else None


def list_events(limit=100, offset=0, format_name=""):
    with database.get_connection() as db:
        where, values = (" WHERE format = ?", (format_name,)) if format_name else ("", ())
        rows = db.execute("SELECT * FROM tournaments" + where + " ORDER BY event_date DESC, created_at DESC LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return [database._row_to_item("tournaments", row) for row in rows]


def choices():
    with database.get_connection() as db:
        users = [dict(row) for row in db.execute("SELECT user_id, name FROM users ORDER BY name COLLATE NOCASE")]
        stores = [dict(row) for row in db.execute("SELECT shop_id, name, address FROM stores ORDER BY name COLLATE NOCASE")]
        decks = [dict(row) for row in db.execute("SELECT deck_id, name FROM decks WHERE published = 1 ORDER BY name COLLATE NOCASE")]
    return {"users": users, "stores": stores, "public_decks": decks, "formats": EVENT_FORMATS}


def text(value, limit, label):
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"Revisa {label}: supera el largo permitido o tiene un formato inválido.")
    return value.strip()


def parse_event(form, previous=None):
    name = text(form.get("name", ""), 200, "el nombre del torneo")
    location = text(form.get("location", ""), 300, "el lugar")
    description = text(form.get("description", ""), 5000, "la descripción")
    format_name = form.get("format", "Casual")
    try:
        event_date = date.fromisoformat(form.get("event_date", "")).isoformat()
    except ValueError:
        raise ValueError("Ingresa una fecha válida para el torneo.") from None
    if not name or format_name not in EVENT_FORMATS:
        raise ValueError("Indica el nombre y un formato válido para el torneo.")
    options = choices()
    store_id = text(form.get("store_id", ""), 120, "la tienda")
    store = next((item for item in options["stores"] if item["shop_id"] == store_id), None)
    if store_id and store is None:
        raise ValueError("La tienda seleccionada ya no existe. Elige otra o escribe el lugar.")
    if not location and not store:
        raise ValueError("Escribe el lugar del torneo o selecciona una tienda registrada.")
    raw = form.get("results_json", "[]")
    if len(raw.encode()) > 2 * 1024 * 1024:
        raise ValueError("Los resultados y listados no deben superar 2 MB.")
    try:
        entries = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("No se pudieron leer los resultados.") from None
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("Puedes registrar hasta 128 resultados por torneo.")
    old = {entry["entry_id"]: entry for entry in (previous or {}).get("results", [])}
    results, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("El formato de un resultado no es válido.")
        try:
            position = int(str(entry.get("position", "")))
        except ValueError:
            raise ValueError("El puesto debe ser un entero entre 1 y 10.000.") from None
        if not 1 <= position <= 10000:
            raise ValueError("El puesto debe estar entre 1 y 10.000.")
        user_id = text(entry.get("user_id", ""), 120, "el usuario")
        user = next((item for item in options["users"] if item["user_id"] == user_id), None)
        if user_id and user is None:
            raise ValueError("El usuario seleccionado no existe. Deja la asociación vacía y escribe su nombre.")
        player_name = user["name"] if user else text(entry.get("player_name", ""), 160, "el jugador")
        if not player_name:
            raise ValueError("Cada resultado necesita un jugador o un usuario registrado.")
        deck_id = text(entry.get("deck_id", ""), 120, "el deck")
        linked = next((item for item in options["public_decks"] if item["deck_id"] == deck_id), None)
        if deck_id and linked is None:
            raise ValueError("El deck seleccionado no está publicado. Elige otro o ingresa su nombre y listado.")
        deck_name = linked["name"] if linked else text(entry.get("deck_name", ""), 200, "el nombre del deck")
        if not deck_name:
            raise ValueError("Cada resultado necesita un nombre de deck o un deck publicado.")
        entry_id = text(entry.get("entry_id", ""), 120, "el identificador de resultado")
        entry_id = entry_id if entry_id in old else uuid4().hex
        if entry_id in seen:
            raise ValueError("Hay un resultado repetido en el formulario.")
        seen.add(entry_id)
        deck_text = text(entry.get("deck_text", ""), 200000, "el listado del deck")
        if deck_id and deck_text:
            raise ValueError("Usa un deck publicado o un listado propio, no ambas opciones.")
        if deck_text:
            previous_entry = old.get(entry_id, {})
            cards = previous_entry["cards"] if deck_text == previous_entry.get("deck_text") else parse_list(deck_text.encode(), "deck.txt", "main")
            if any(card["status"] == "error" and not card.get("error", "").startswith("No encontrada.") for card in cards):
                raise ValueError(f"Revisa las cantidades y el formato del listado de {player_name}.")
        else:
            cards = []
        results.append({"entry_id": entry_id, "position": position, "player_name": player_name, "user_id": user_id,
                        "deck_name": deck_name, "deck_id": deck_id, "deck_text": deck_text, "cards": cards})
    return {"name": name, "event_date": event_date, "format": format_name, "description": description,
            "location": location or store.get("address") or store["name"], "store_id": store_id,
            "store_name": store["name"] if store else "", "results": sorted(results, key=lambda item: item["position"])}


def save_event(fields, previous=None):
    now = datetime.now().isoformat(timespec="seconds")
    if previous:
        event_id, revision = previous["tournament_id"], previous["revision"]
        with database.get_connection() as db:
            updated = db.execute("UPDATE tournaments SET name=?, event_date=?, format=?, location=?, store_id=?, store_name=?, description=?, results=?, revision=revision+1, updated_at=? WHERE tournament_id=? AND revision=?",
                (*[fields[key] for key in ("name", "event_date", "format", "location", "store_id", "store_name", "description")], json.dumps(fields["results"], ensure_ascii=False), now, event_id, revision))
            if not updated.rowcount:
                raise ValueError("El torneo cambió en otra pestaña. Recarga antes de guardar.")
    else:
        event_id = uuid4().hex
        with database.get_connection() as db:
            db.execute("INSERT INTO tournaments (position,tournament_id,name,event_date,format,location,store_id,store_name,description,results,revision,created_at,updated_at) VALUES (0,?,?,?,?,?,?,?,?,?,0,?,?)",
                (event_id, *[fields[key] for key in ("name", "event_date", "format", "location", "store_id", "store_name", "description")], json.dumps(fields["results"], ensure_ascii=False), now, now))
    return event_id


def register_tournament_routes(app, require_admin):
    routes = Blueprint("tournaments", __name__, url_prefix="/tournaments")

    def event_or_404(event_id):
        event = get_event(event_id)
        if not event:
            abort(404)
        return event

    def editor(event=None, error=None, submitted=None):
        return render_template("tournament_manage.html", event=event, options=choices(), events=list_events(),
                               error=error, submitted=submitted, search_query="")

    @routes.get("")
    def index():
        format_name = request.args.get("format", "")
        if format_name and format_name not in EVENT_FORMATS:
            abort(400)
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        events = list_events(25, (page - 1) * 24, format_name)
        return render_template("tournaments.html", events=events[:24], has_next=len(events) > 24,
                               page=page, format_filter=format_name, formats=EVENT_FORMATS, search_query="")

    @routes.get("/<event_id>")
    def detail(event_id):
        event = event_or_404(event_id)
        opts = choices()
        return render_template("tournament_detail.html", event=event,
            user_ids={item["user_id"] for item in opts["users"]}, store_ids={item["shop_id"] for item in opts["stores"]},
            deck_ids={item["deck_id"] for item in opts["public_decks"]}, search_query="")

    @routes.route("/manage", methods=["GET", "POST"])
    def manage():
        require_admin()
        event = event_or_404(request.form["tournament_id"]) if request.method == "POST" and request.form.get("tournament_id") else None
        if request.method == "POST":
            try:
                if event and str(event["revision"]) != request.form.get("revision"):
                    raise ValueError("El torneo cambió en otra pestaña. Recarga antes de guardar.")
                fields = parse_event(request.form, event)
                event_id = save_event(fields, event)
                flash("Torneo guardado. Los listados se validarán a continuación.", "success")
                return redirect(url_for("tournaments.manage", edit=event_id))
            except ValueError as exc:
                return editor(event, str(exc), request.form), 400
        if request.args.get("edit"):
            event = event_or_404(request.args["edit"])
        return editor(event)

    @routes.post("/manage/<event_id>/resolve")
    def resolve(event_id):
        require_admin()
        event = event_or_404(event_id)
        if str(event["revision"]) != request.form.get("revision"):
            return jsonify(error="El torneo cambió. Recarga para continuar."), 409
        entry = next((entry for entry in event["results"] if any(row["status"] == "pending" for row in entry["cards"])), None)
        try:
            if entry:
                entry["cards"] = resolve_rows(entry["cards"])
                save_event(event, event)
                event["revision"] += 1
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 502
        return jsonify(revision=event["revision"], pending=sum(row["status"] == "pending" for entry in event["results"] for row in entry["cards"]),
                       errors=[f"{entry['player_name']}: {row['name']} — {row['error']}" for entry in event["results"] for row in entry["cards"] if row["status"] == "error"])

    @routes.post("/manage/<event_id>/delete")
    def delete(event_id):
        require_admin()
        event = event_or_404(event_id)
        if str(event["revision"]) != request.form.get("revision"):
            abort(409)
        with database.get_connection() as db:
            deleted = db.execute("DELETE FROM tournaments WHERE tournament_id=? AND revision=?", (event_id, event["revision"]))
            if not deleted.rowcount:
                abort(409)
        flash("Torneo eliminado.", "success")
        return redirect(url_for("tournaments.manage"))

    def entry_deck(event_id, entry_id):
        event = event_or_404(event_id)
        entry = next((entry for entry in event["results"] if entry["entry_id"] == entry_id and entry["cards"]), None)
        if not entry:
            abort(404)
        return event, entry, {"deck_id": entry_id, "name": entry["deck_name"], "description": f"{event['name']} · Puesto {entry['position']}",
            "format": event["format"] if event["format"] in FORMATS else "Casual", "published": True, "author_name": entry["player_name"],
            "author_profile_url": url_for("user_profile_page", user_id=entry["user_id"]) if entry["user_id"] and any(user["user_id"] == entry["user_id"] for user in choices()["users"]) else "", "cards": entry["cards"]}

    @routes.get("/<event_id>/decks/<entry_id>")
    def deck_detail(event_id, entry_id):
        event, entry, deck = entry_deck(event_id, entry_id)
        return render_template("deck_detail.html", deck=deck, boards=BOARDS, readonly=True, search_query="",
            analysis_url=url_for("tournaments.deck_analysis", event_id=event_id, entry_id=entry_id),
            back_url=url_for("tournaments.detail", event_id=event_id), back_label="Torneo")

    @routes.get("/<event_id>/decks/<entry_id>/analysis")
    def deck_analysis(event_id, entry_id):
        _, _, deck = entry_deck(event_id, entry_id)
        try:
            return jsonify(analyze(deck))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 502

    app.register_blueprint(routes)
