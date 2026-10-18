"""Cached USD estimates and Commander Game Changer lower bounds, never a power score."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import time

import bulk_import
import database

TTL_SECONDS = 86400
BRACKETS_SOURCE = "https://magic.wizards.com/en/formats/commander"


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result < Decimal("1000000000") else None
    except (InvalidOperation, ValueError):
        return None


def market_data(card, now=None):
    prices = card.get("prices") or {}
    return {"prices": {key: str(amount(prices.get(key))) if amount(prices.get(key)) is not None else None
                       for key in ("usd", "usd_foil", "usd_etched")},
            "game_changer": card.get("game_changer") if isinstance(card.get("game_changer"), bool) else None,
            "market_checked_at": time.time() if now is None else now}


def summarize(deck, market):
    playable = [row for row in deck["cards"] if row["board"] in {"main", "commander", "side"}]
    commander_rows = [row for row in playable if row["board"] != "side"]
    total = Decimal(0)
    priced, missing, foil = 0, 0, 0
    gc = {}
    unknown_gc = 0
    dates = []
    for row in playable:
        info = market.get(row.get("card", {}).get("id"), {}) if row["status"] == "ready" else {}
        price = None
        for finish in ("usd", "usd_foil", "usd_etched"):
            price = amount((info.get("prices") or {}).get(finish))
            if price is not None:
                if finish != "usd":
                    foil += row["quantity"]
                break
        if price is None:
            missing += row["quantity"]
        else:
            total += price * row["quantity"]
            priced += row["quantity"]
        if info.get("market_checked_at"):
            dates.append(info["market_checked_at"])
        if row["board"] in {"main", "commander"}:
            if info.get("game_changer") is True:
                name = row.get("card", {}).get("name", row["name"])
                if name not in gc:
                    gc[name] = {"name": name, "quantity": 0, "card_id": row["card"]["id"], "image_url": row["card"].get("image_url", "")}
                gc[name]["quantity"] += row["quantity"]
            elif info.get("game_changer") is not False:
                unknown_gc += row["quantity"]
    count = sum(item["quantity"] for item in gc.values())
    return {"usd_total": str(total.quantize(Decimal("0.01"))) if priced else None, "priced_copies": priced,
            "missing_price_copies": missing, "foil_price_copies": foil, "is_partial": missing > 0,
            "price_checked_at": datetime.fromtimestamp(min(dates), timezone.utc).isoformat() if dates else None,
            "commander": deck["format"] == "Commander", "game_changers": sorted(gc.values(), key=lambda item: item["name"]),
            "game_changer_count": count, "unknown_game_changer_copies": unknown_gc,
            "bracket_minimum": None if deck["format"] != "Commander" or unknown_gc or not commander_rows else 4 if count > 3 else 3 if count else 1,
            "brackets_source": BRACKETS_SOURCE}


def analyze(deck):
    now = time.time()
    cards = {row["card"]["id"]: row["card"] for row in deck["cards"] if row["status"] == "ready" and row.get("card") and row["board"] != "maybe"}
    market = {}
    if cards:
        with database.get_connection() as db:
            rows = db.execute("SELECT card_id, data, checked_at FROM deck_card_market WHERE card_id IN (" + ",".join("?" for _ in cards) + ")", tuple(cards)).fetchall()
        for row in rows:
            if row["checked_at"] >= now - TTL_SECONDS:
                market[row["card_id"]] = json.loads(row["data"])
        for card_id, card in cards.items():
            if card_id not in market and isinstance(card.get("market_checked_at"), (int, float)) and card["market_checked_at"] >= now - TTL_SECONDS:
                market[card_id] = {key: card.get(key) for key in ("prices", "game_changer", "market_checked_at")}
    pending = [card_id for card_id in cards if card_id not in market]
    batch = pending[:25]
    if batch:
        fetched = bulk_import.fetch_collection([{"id": card_id} for card_id in batch])
        found = {card["id"]: market_data(card, now) for card in fetched if card.get("id") in batch}
        with database.get_connection() as db:
            for card_id in batch:
                # Cache missing entries too, to avoid repeated requests for unavailable cards.
                info = found.get(card_id, {"prices": {}, "game_changer": None, "market_checked_at": now})
                market[card_id] = info
                db.execute("INSERT INTO deck_card_market (position, card_id, data, checked_at) VALUES (0, ?, ?, ?) ON CONFLICT(card_id) DO UPDATE SET data = excluded.data, checked_at = excluded.checked_at",
                           (card_id, json.dumps(info), now))
    return {**summarize(deck, market), "pending": max(0, len(pending) - len(batch))}
