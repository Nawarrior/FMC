import sqlite3
import time
import unittest
from unittest.mock import patch

import database
import decks
import deck_insights as insights

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


def row(name="Sol Ring", quantity=1, board="main", price="1.00", gc=False, card_id=None):
    return {"row_id": name, "name": name, "quantity": quantity, "board": board, "status": "ready",
            "card": {"id": card_id or name, "name": name, "image_url": "https://example.test/card.jpg",
                     **insights.market_data({"prices": {"usd": price}, "game_changer": gc})}}


class InsightCalculationTests(unittest.TestCase):
    def summarize(self, rows, format_name="Commander"):
        return insights.summarize({"cards": rows, "format": format_name}, {entry["card"]["id"]: entry["card"] for entry in rows if entry.get("card")})

    def test_prices_multiply_quantities_and_include_side_not_considering(self):
        result = self.summarize([row("A", 2, price="0.10"), row("B", price="0.20", board="commander"),
                                 row("C", price="0.30", board="side"), row("D", price="100", board="maybe")])
        self.assertEqual(result["usd_total"], "0.70")
        self.assertEqual(result["priced_copies"], 4)
        self.assertFalse(result["is_partial"])

    def test_missing_digital_and_unresolved_cards_do_not_count_as_free(self):
        result = self.summarize([row("A", price="2.00"), row("Digital", 3, price=None),
            {"row_id": "pending", "name": "Pending", "board": "main", "quantity": 2, "status": "pending"}])
        self.assertEqual(result["usd_total"], "2.00")
        self.assertEqual(result["missing_price_copies"], 5)
        self.assertTrue(result["is_partial"])
        self.assertIsNone(result["bracket_minimum"])
        self.assertIsNone(self.summarize([row(price=None)])["usd_total"])
        self.assertIsNone(self.summarize([])["usd_total"])

    def test_regular_price_preferred_with_foil_fallback(self):
        entry = row(price="1.50")
        entry["card"]["prices"]["usd_foil"] = "1.00"
        self.assertEqual(self.summarize([entry])["usd_total"], "1.50")
        entry["card"]["prices"]["usd"] = None
        self.assertEqual(self.summarize([entry])["usd_total"], "1.00")
        self.assertEqual(self.summarize([entry])["foil_price_copies"], 1)
        for value in ("NaN", "Infinity", "-1", "invalid", True):
            self.assertIsNone(insights.amount(value))

    def test_game_changer_minimum_is_not_a_definitive_power_bracket(self):
        self.assertEqual(self.summarize([row()])["bracket_minimum"], 1)
        entries = [row("Rhystic Study", gc=True), row("Farewell", gc=True, board="commander"),
                   row("Demonic Tutor", gc=True), row("The One Ring", gc=True, board="side")]
        result = self.summarize(entries)
        self.assertEqual(result["game_changer_count"], 3)
        self.assertEqual(result["bracket_minimum"], 3)
        self.assertNotIn("The One Ring", [entry["name"] for entry in result["game_changers"]])
        entries[-1]["board"] = "main"
        self.assertEqual(self.summarize(entries)["bracket_minimum"], 4)
        self.assertIsNone(self.summarize(entries, "Brawl")["bracket_minimum"])
        self.assertFalse(self.summarize(entries, "Brawl")["commander"])


class InsightCacheTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        patcher = patch.object(database, "get_connection", return_value=self.connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        database.initialize_database()

    def test_fresh_card_metadata_does_not_make_network_requests(self):
        deck = {"cards": [row()], "format": "Modern"}
        with patch("bulk_import.fetch_collection", side_effect=AssertionError("No request expected")):
            result = insights.analyze(deck)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["usd_total"], "1.00")

    def test_existing_cards_refresh_in_batches_and_cache_null_prices(self):
        entries = [row(str(index)) for index in range(27)]
        for entry in entries:
            entry["card"].pop("market_checked_at")
        deck = {"cards": entries, "format": "Commander"}
        def fetch(identifiers):
            return [{"id": identifier["id"], "prices": {"usd": None}, "game_changer": False} for identifier in identifiers]
        with patch("bulk_import.fetch_collection", side_effect=fetch) as api:
            self.assertEqual(insights.analyze(deck)["pending"], 2)
            self.assertEqual(len(api.call_args.args[0]), 25)
            result = insights.analyze(deck)
            self.assertEqual(result["pending"], 0)
            self.assertEqual(result["missing_price_copies"], 27)
            insights.analyze(deck)
            self.assertEqual(api.call_count, 2)
        self.connection.execute("UPDATE deck_card_market SET checked_at = ?", (time.time() - insights.TTL_SECONDS - 1,))
        with patch("bulk_import.fetch_collection", side_effect=fetch) as api:
            insights.analyze(deck)
            self.assertEqual(api.call_count, 1)

    def test_access_rules_and_no_deck_revision_changes(self):
        application.app.config.update(TESTING=True)
        client = application.app.test_client()
        user = {"user_id": "owner", "name": "Owner", "email": "owner@example.test", "role": "adm"}
        database.save_collection("users", [user])
        deck_id = decks.create_deck("owner", {"name": "Price", "format": "Commander", "description": ""})
        deck = decks.get_deck(deck_id, "owner")
        deck["cards"] = [row(gc=True)]
        deck["published"] = True
        decks.save_deck(deck, 0)
        with patch("bulk_import.fetch_collection", side_effect=AssertionError("Cached metadata")):
            response = client.get("/decks/public/" + deck_id + "/analysis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["usd_total"], "1.00")
        self.assertEqual(response.json["game_changer_count"], 1)
        self.assertEqual(decks.get_deck(deck_id, "owner")["revision"], 1)
        self.assertEqual(client.get("/decks/" + deck_id + "/analysis").status_code, 401)
        with client.session_transaction() as session:
            session["user"] = user
        self.assertEqual(client.get("/decks/" + deck_id + "/analysis").status_code, 200)
        deck["published"] = False
        decks.save_deck(deck, 1)
        self.assertEqual(client.get("/decks/public/" + deck_id + "/analysis").status_code, 404)


if __name__ == "__main__":
    unittest.main()
