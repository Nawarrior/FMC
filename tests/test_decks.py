import io
import json
import sqlite3
import unittest
from unittest.mock import patch

import database
import decks

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


class DeckTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        patcher = patch.object(database, "get_connection", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # app's imported connection is used by news tests and globals.
        patcher = patch.object(application, "get_connection", return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        database.initialize_database()
        self.users = [{"user_id": name, "name": name, "email": name + "@example.test", "role": "adm" if name == "owner" else "usuario"} for name in ("owner", "other")]
        database.save_collection("users", self.users)
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()
        self.deck_id = decks.create_deck("owner", {"name": "Mi deck", "format": "Modern", "description": "Prueba"})
        self.base = "/decks/" + self.deck_id
        self.login()
        self.card = {"id": "card-id", "name": "Lightning Bolt", "set": "m11", "collector_number": "146", "type_line": "Instant", "cmc": 1, "mana_cost": "{R}", "colors": ["R"], "oracle_text": "Deals 3 damage.", "image_uris": {"normal": "https://example.test/card.jpg", "art_crop": "https://example.test/art.jpg"}}

    def login(self, who="owner"):
        with self.client.session_transaction() as session:
            session["user"] = next(user for user in self.users if user["user_id"] == who)
            session["_csrf_token"] = "test"

    def post(self, path, **fields):
        deck = decks.get_deck(self.deck_id, "owner")
        return self.client.post(self.base + path, data={"csrf_token": "test", "revision": deck["revision"] if deck else 0, **fields})

    def test_private_views_auth_and_csrf(self):
        self.assertEqual(self.client.get("/decks").status_code, 200)
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"deck-view-switch", response.data)
        self.assertIn(b"Mis decks", response.data)
        self.assertEqual(self.client.post(self.base + "/edit", data={"name": "x"}).status_code, 400)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/decks").status_code, 302)
        self.assertEqual(self.client.get(self.base).status_code, 401)

    def test_other_users_cannot_read_export_or_mutate_deck(self):
        self.login("other")
        for suffix in ("", "/data", "/export"):
            self.assertEqual(self.client.get(self.base + suffix).status_code, 404)
        for suffix in ("/import", "/resolve", "/retry", "/edit", "/delete", "/clear", "/publish", "/cards/fake"):
            self.assertEqual(self.post(suffix).status_code, 404)
        self.assertNotIn(b'Mi deck', self.client.get("/decks").data)

    def test_create_metadata_and_delete(self):
        response = self.client.post("/decks", data={"csrf_token": "test", "name": "New", "format": "Commander"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(decks.list_decks("owner")), 2)
        self.assertEqual(self.post("/edit", name="Nuevo nombre", format="Pauper", description="Plan").status_code, 200)
        self.assertEqual(decks.get_deck(self.deck_id, "owner")["name"], "Nuevo nombre")
        self.assertEqual(self.post("/edit", name="", format="bad").status_code, 400)
        self.assertEqual(self.post("/delete").status_code, 200)
        self.assertIsNone(decks.get_deck(self.deck_id, "owner"))

    def test_text_formats_boards_printings_and_flags(self):
        raw = b'Commander\n1 Atraxa, Praetors\' Voice\n\nDeck\n4 Lightning Bolt (M11) 146\n2x Forest\nSideboard (15)\n1 Duress\nMaybeboard\n1 Thoughtseize\n1 Sol Ring *CMDR*\nSB: 2 Opt\n'
        rows = decks.parse_list(raw, "test.txt", "main")
        self.assertEqual([row["board"] for row in rows], ["commander", "main", "main", "side", "maybe", "commander", "side"])
        self.assertEqual(rows[1]["set"], "m11")
        self.assertEqual(rows[1]["number"], "146")
        self.assertEqual(rows[2]["quantity"], 2)
        self.assertEqual(len({row["row_id"] for row in rows}), len(rows))

    def test_import_resolve_merge_and_quantity_board_edit(self):
        response = self.post("/import", cards_text="2 Lightning Bolt\n2 Lightning Bolt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["cards"]), 2)
        with patch("bulk_import.fetch_collection", return_value=[self.card]) as fetch:
            response = self.post("/resolve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch.call_args.args[0], [{"name": "Lightning Bolt"}])
        rows = response.json["cards"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 4)
        self.assertEqual(rows[0]["card"]["mana_cost"], "{R}")
        self.assertEqual(database.load_collection("saved_cards"), [])
        self.assertEqual(database.load_collection("card_notifications"), [])
        response = self.post("/cards/" + rows[0]["row_id"], quantity="3", board="side")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["cards"][0]["board"], "side")
        self.assertEqual(response.json["cards"][0]["quantity"], 3)
        self.assertEqual(self.post("/cards/" + rows[0]["row_id"], quantity="0").status_code, 400)
        self.assertEqual(self.post("/cards/" + rows[0]["row_id"], action="delete").json["cards"], [])

    def test_missing_cards_can_be_corrected_and_network_errors_retried(self):
        self.post("/import", cards_text="1 Incorrect name")
        with patch("bulk_import.fetch_collection", side_effect=RuntimeError("Try again")):
            self.assertEqual(self.post("/resolve").status_code, 502)
        self.assertEqual(decks.get_deck(self.deck_id, "owner")["cards"][0]["status"], "pending")
        with patch("bulk_import.fetch_collection", return_value=[]):
            row = self.post("/resolve").json["cards"][0]
        self.assertEqual(row["status"], "error")
        self.post("/cards/" + row["row_id"], name="Lightning Bolt")
        with patch("bulk_import.fetch_collection", return_value=[self.card]):
            row = self.post("/resolve").json["cards"][0]
        self.assertEqual(row["status"], "ready")

    def test_resolution_is_chunked_and_no_network_on_get(self):
        self.post("/import", cards_text="\n".join("1 Test " + str(i) for i in range(27)))
        with patch("bulk_import.fetch_collection", return_value=[]) as fetch:
            response = self.post("/resolve")
            self.assertEqual(len(fetch.call_args.args[0]), 25)
        self.assertEqual(sum(row["status"] == "pending" for row in response.json["cards"]), 2)
        with patch("bulk_import.fetch_collection", side_effect=AssertionError("No network on GET")):
            self.assertEqual(self.client.get(self.base).status_code, 200)

    def test_stale_revision_does_not_overwrite_changes(self):
        stale = decks.get_deck(self.deck_id, "owner")
        self.post("/edit", name="Updated", format="Modern")
        self.assertEqual(self.post("/import", revision=0, cards_text="1 Forest").status_code, 409)
        stale["name"] = "Stale"
        with self.assertRaises(decks.Conflict):
            decks.save_deck(stale, 0)
        self.assertEqual(decks.get_deck(self.deck_id, "owner")["name"], "Updated")

    def test_export_roundtrip_and_csv_upload(self):
        response = self.post("/import", file=(io.BytesIO(b'Nombre,Cantidad\nLightning Bolt,4'), "deck.csv"), board="side")
        self.assertEqual(response.status_code, 200)
        with patch("bulk_import.fetch_collection", return_value=[self.card]):
            self.post("/resolve")
        exported = self.client.get(self.base + "/export")
        self.assertEqual(exported.status_code, 200)
        rows = decks.parse_list(exported.data, "deck.txt", "main")
        self.assertEqual(rows[0]["quantity"], 4)
        self.assertEqual(rows[0]["board"], "side")
        self.assertEqual(rows[0]["set"], "m11")

    def test_import_limits_and_invalid_input_preserve_deck(self):
        for fields in ({"cards_text": ""}, {"cards_text": "1 Forest", "board": "bad"}, {"cards_text": "\n".join("1 Forest" for _ in range(501))}):
            self.assertEqual(self.post("/import", **fields).status_code, 400)
        response = self.post("/import", file=(io.BytesIO(b"1 Forest"), "cards.exe"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(decks.get_deck(self.deck_id, "owner")["cards"], [])

    def test_card_metadata_is_escaped_in_rendered_json(self):
        self.post("/edit", name="<script>alert(1)</script>", format="Modern")
        response = self.client.get(self.base)
        self.assertNotIn(b'<h1 id="deck-name"><script>', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)

    def test_arena_printing_falls_back_to_exact_name_and_preserves_quantity(self):
        self.post("/import", cards_text="4 Lightning Bolt (SPG) 0")
        with patch("bulk_import.fetch_collection", side_effect=[[], [self.card]]) as fetch:
            response = self.post("/resolve")
        self.assertEqual(fetch.call_args_list[0].args[0], [{"set": "spg", "collector_number": "0"}])
        self.assertEqual(fetch.call_args_list[1].args[0], [{"name": "Lightning Bolt"}])
        row = response.json["cards"][0]
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["quantity"], 4)
        self.assertEqual(row["set"], "m11")
        self.assertIn("SPG #0", row["edition_note"])

    def test_fallback_does_not_substitute_a_different_card_and_missing_can_retry(self):
        self.post("/import", cards_text="1 Wrong card (SPG) 0")
        with patch("bulk_import.fetch_collection", side_effect=[[], [self.card]]):
            response = self.post("/resolve")
        self.assertEqual(response.json["cards"][0]["status"], "error")
        response = self.post("/retry")
        self.assertEqual(response.json["cards"][0]["status"], "pending")
        self.assertEqual(response.json["cards"][0]["name"], "Wrong card")

    def logout(self):
        with self.client.session_transaction() as session:
            session.clear()

    def test_decks_start_private_and_navigation_is_session_aware(self):
        self.assertFalse(decks.get_deck(self.deck_id, "owner")["published"])
        self.assertIn(b'href="/decks">Mis decks', self.client.get("/decks").data)
        self.logout()
        response = self.client.get("/decks/public")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/decks">Mis decks', response.data)
        self.assertIn(b'Decks publicados', response.data)
        self.assertNotIn(b'Mi deck', response.data)
        self.assertEqual(self.client.get("/decks/public/" + self.deck_id).status_code, 404)

    def test_published_deck_is_public_readonly_and_hides_private_fields(self):
        self.post("/import", cards_text="4 Lightning Bolt")
        with patch("bulk_import.fetch_collection", return_value=[self.card]):
            self.post("/resolve")
        self.assertEqual(self.post("/publish", published="1").status_code, 200)
        self.logout()
        self.assertIn(b'Mi deck', self.client.get("/decks/public").data)
        response = self.client.get("/decks/public/" + self.deck_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-readonly="true"', response.data)
        self.assertIn(b'Lightning Bolt', response.data)
        self.assertIn(b'href="/user/owner">owner</a>', response.data)
        self.assertNotIn(b'owner@example.test', response.data)
        self.assertNotIn(b'"user_id"', response.data)
        for control in (b'id="deck-import-form"', b'id="deck-add-form"', b'id="deck-metadata-form"', b'id="deck-publish"', b'id="deck-delete"'):
            self.assertNotIn(control, response.data)
        self.login()
        self.assertNotIn(b'id="deck-metadata-form"', self.client.get("/decks/public/" + self.deck_id).data)

    def test_format_filter_lists_all_authors_and_only_published(self):
        self.post("/publish", published="1")
        second = decks.create_deck("other", {"name": "Other Commander", "format": "Commander", "description": ""})
        second_deck = decks.get_deck(second, "other")
        second_deck["published"] = True
        decks.save_deck(second_deck, 0)
        decks.create_deck("other", {"name": "Private Commander", "format": "Commander", "description": ""})
        self.logout()
        response = self.client.get("/decks/public")
        self.assertIn(b'Mi deck', response.data)
        self.assertIn(b'Other Commander', response.data)
        self.assertNotIn(b'Private Commander', response.data)
        response = self.client.get("/decks/public?format=Commander")
        self.assertIn(b'Other Commander', response.data)
        self.assertNotIn(b'Mi deck', response.data)
        self.assertEqual(self.client.get("/decks/public?format=invalid").status_code, 400)

    def test_unpublish_revokes_public_access_and_other_users_cannot_edit(self):
        self.post("/publish", published="1")
        self.login("other")
        self.assertEqual(self.client.get("/decks/public/" + self.deck_id).status_code, 200)
        self.assertEqual(self.post("/publish", published="0").status_code, 404)
        self.assertEqual(self.post("/edit", name="Overwrite", format="Modern").status_code, 404)
        self.assertEqual(self.client.get(self.base + "/data").status_code, 404)
        self.login()
        self.assertEqual(self.post("/publish", published="0").status_code, 200)
        self.logout()
        self.assertEqual(self.client.get("/decks/public/" + self.deck_id).status_code, 404)
        self.assertNotIn(b'Mi deck', self.client.get("/decks/public").data)

    def test_publish_requires_csrf_and_revision_and_survives_edits(self):
        self.assertEqual(self.client.post(self.base + "/publish", data={"published": "1", "revision": "0"}).status_code, 400)
        self.assertEqual(self.post("/publish", published="bad").status_code, 400)
        self.post("/publish", published="1")
        self.assertEqual(self.post("/publish", published="0", revision="0").status_code, 409)
        self.post("/edit", name="Still public", format="Pauper")
        self.assertTrue(decks.get_deck(self.deck_id, "owner")["published"])

    def test_public_catalog_pagination(self):
        for i in range(25):
            deck_id = decks.create_deck("other", {"name": "Published " + str(i), "format": "Commander", "description": ""})
            deck = decks.get_deck(deck_id, "other")
            deck["published"] = True
            decks.save_deck(deck, 0)
        self.logout()
        first = self.client.get("/decks/public?format=Commander")
        second = self.client.get("/decks/public?format=Commander&page=2")
        self.assertEqual(first.data.count(b'class="deck-library-card"'), 24)
        self.assertEqual(second.data.count(b'class="deck-library-card"'), 1)
        self.assertIn(b'format=Commander', first.data)

    def test_public_author_links_and_combined_user_format_filter(self):
        self.post("/publish", published="1")
        second = decks.create_deck("other", {"name": "Deck de otro autor", "format": "Modern", "description": ""})
        other = decks.get_deck(second, "other")
        other["published"] = True
        decks.save_deck(other, 0)
        self.logout()
        response = self.client.get("/decks/public?user_id=owner&format=Modern")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Mi deck', response.data)
        self.assertNotIn(b'Deck de otro autor', response.data)
        self.assertIn(b'href="/user/owner">owner</a>', response.data)
        self.assertIn(b'value="owner" selected', response.data)
        response = self.client.get("/decks/public?user_id=owner&format=Commander")
        self.assertNotIn(b'Mi deck', response.data)
        response = self.client.get("/decks/public/" + self.deck_id)
        self.assertIn(b'href="/user/owner">owner</a>', response.data)
        self.assertNotIn(b'id="deck-edit-toggle"', response.data)

    def test_profile_shortcuts_and_user_card_filter_without_network(self):
        def saved(name, owner, intent, **extras):
            return {"card_id": name.lower(), "name": name, "user_id": owner, "user_name": owner,
                    "user_email": owner + "@example.test", "intent": intent, "quantity": 1,
                    "transaction_completed": False, **extras}
        database.save_collection("saved_cards", [saved("Forest", "owner", "busco"), saved("Island", "owner", "ofrezco"),
            saved("Swamp", "other", "busco"), saved("Mountain", "owner", "busco", transaction_completed=True),
            saved("Plains", "owner", "busco", user_id="")])
        self.post("/publish", published="1")
        self.logout()
        with patch.object(application, "get_card", side_effect=AssertionError("No eager card fetch")):
            response = self.client.get("/user/owner")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'/community-cards?user_id=owner&amp;status=busco', response.data)
            self.assertIn(b'/community-cards?user_id=owner&amp;status=ofrezco', response.data)
            self.assertIn(b'/decks/public?user_id=owner', response.data)
            self.assertIn(b'1 mazos p', response.data)
            wanted = self.client.get("/community-cards?user_id=owner&status=busco")
            self.assertEqual(wanted.status_code, 200)
            self.assertIn(b'>Forest</a>', wanted.data)
            self.assertIn(b'>Plains</a>', wanted.data)
            self.assertNotIn(b'>Island</a>', wanted.data)
            self.assertNotIn(b'>Swamp</a>', wanted.data)
            self.assertNotIn(b'>Mountain</a>', wanted.data)
            offered = self.client.get("/community-cards?user_id=owner&status=ofrezco")
            self.assertIn(b'>Island</a>', offered.data)
            self.assertNotIn(b'>Forest</a>', offered.data)
        self.assertEqual(self.client.get("/community-cards?user_id=missing").status_code, 404)

    def test_list_upload_replaces_previous_cards_and_zones_without_adding(self):
        self.post("/import", cards_text="4 Lightning Bolt\nSideboard\n2 Negate")
        response = self.post("/import", cards_text="2 Forest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(row["name"], row["quantity"], row["board"]) for row in response.json["cards"]], [("Forest", 2, "main")])
        response = self.post("/import", cards_text="2 Forest")
        self.assertEqual(sum(row["quantity"] for row in response.json["cards"]), 2)
        response = self.post("/import", mode="append", cards_text="1 Island")
        self.assertEqual([row["name"] for row in response.json["cards"]], ["Forest", "Island"])

    def test_invalid_replacement_keeps_previous_list(self):
        self.post("/import", cards_text="4 Lightning Bolt")
        before = decks.get_deck(self.deck_id, "owner")
        for fields in ({"cards_text": ""}, {"cards_text": "0 Forest"}, {"cards_text": "1 Forest", "mode": "unknown"}):
            self.assertEqual(self.post("/import", **fields).status_code, 400)
            self.assertEqual(decks.get_deck(self.deck_id, "owner"), before)

    def test_clear_only_removes_cards_preserving_deck_metadata_and_publication(self):
        self.post("/import", cards_text="4 Lightning Bolt")
        self.post("/publish", published="1")
        before = decks.get_deck(self.deck_id, "owner")
        self.assertEqual(self.client.post(self.base + "/clear", data={"revision": before["revision"]}).status_code, 400)
        self.assertEqual(self.post("/clear", revision=0).status_code, 409)
        response = self.post("/clear")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["cards"], [])
        for key in ("name", "format", "description", "published", "deck_id"):
            self.assertEqual(response.json[key], before[key])
        self.assertEqual(response.json["revision"], before["revision"] + 1)

    def test_insights_are_inside_single_deck_header_and_clear_not_public(self):
        response = self.client.get(self.base)
        content = response.get_data(as_text=True)
        header = content.index('<header class="deck-hero">')
        self.assertLess(content.index('class="deck-insights"', header), content.index('</header>', header))
        self.assertIn('id="deck-clear"', content)
        self.assertIn('name="mode" value="replace"', content)
        self.post("/publish", published="1")
        response = self.client.get("/decks/public/" + self.deck_id)
        self.assertNotIn(b'id="deck-clear"', response.data)


if __name__ == "__main__":
    unittest.main()
