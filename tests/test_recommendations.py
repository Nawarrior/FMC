import unittest
from urllib.error import HTTPError
from unittest.mock import patch

# Import without initializing or migrating the user's database.
with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


def card(card_id, name, **values):
    return {
        "id": card_id, "oracle_id": name, "name": name,
        "type_line": "Creature — Elf Druid", "colors": ["G"],
        "cmc": 2, "keywords": ["Reach"], **values,
    }


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.selected = card("selected", "Selected Elf")
        self.candidates = [card(str(i), f"Elf {i}") for i in range(3)]
        application.app.config.update(TESTING=True)

    def test_ranks_similar_cards_and_excludes_other_printings(self):
        less_similar = card("other", "Other Creature", colors=["R"],
                            type_line="Creature — Goblin", cmc=6, keywords=[])
        alternate_print = card("reprint", self.selected["name"])
        with patch.object(application, "fetch_json", return_value={
            "data": [less_similar, alternate_print, *self.candidates, self.candidates[0]],
        }) as fetch:
            result = application.get_similar_cards(self.selected)
        self.assertEqual([item["id"] for item in result], ["0", "1", "2"])
        fetch.assert_called_once()

    def test_broadens_empty_search_and_keeps_results_unique(self):
        no_matches = RuntimeError("No matches")
        no_matches.__cause__ = HTTPError("https://api.scryfall.com", 404, "Not found", {}, None)
        with patch.object(application, "fetch_json", side_effect=[
            no_matches, {"data": self.candidates[:1]}, {"data": self.candidates},
        ]) as fetch, patch.object(application.time, "sleep"):
            result = application.get_similar_cards(self.selected)
        self.assertEqual(len(result), 3)
        self.assertEqual(fetch.call_count, 3)

    def test_double_faced_and_colorless_cards(self):
        selected = card("double", "Double Card", type_line="Creature // Land",
                        colors=[], card_faces=[{"type_line": "Artifact Creature — Golem"}])
        types, subtypes, colors, _, _ = application.card_similarity_features(selected)
        self.assertEqual(types, {"artifact", "creature"})
        self.assertEqual(subtypes, {"golem"})
        self.assertEqual(colors, set())
        with patch.object(application, "fetch_json", return_value={"data": self.candidates}) as fetch:
            application.get_similar_cards(selected)
        self.assertIn("c%3Dc", fetch.call_args.args[0])

    def test_prefers_similar_spell_effects(self):
        selected = card("bolt", "Lightning Bolt", type_line="Instant", colors=["R"],
                        keywords=[], cmc=1, oracle_text="Lightning Bolt deals 3 damage to any target.")
        damage = card("shock", "Shock", type_line="Instant", colors=["R"], keywords=[],
                      cmc=1, oracle_text="Shock deals 2 damage to any target.")
        unrelated = card("draw", "A different spell", type_line="Instant", colors=["R"],
                         keywords=[], cmc=1, oracle_text="Discard a card, then draw two cards.")
        with patch.object(application, "fetch_json", return_value={"data": [unrelated, damage]}), \
             patch.object(application.time, "sleep"):
            result = application.get_similar_cards(selected)
        self.assertEqual(result[0]["name"], "Shock")

    def test_normalizes_effect_word_forms(self):
        selected = card("effect", "Effect", oracle_text="That player discards a card, draws a card, then exiles a creature.")
        self.assertEqual(application.card_effect_terms(selected), {"discard", "draw", "exile"})

    def test_duress_finds_thoughtseize_on_later_page(self):
        def discard_spell(card_id, name, text):
            return card(card_id, name, type_line="Sorcery", colors=["B"],
                        cmc=1, keywords=[], oracle_text=text)

        selected = discard_spell("duress", "Duress", "Target opponent reveals their hand. You choose a noncreature, nonland card from it. That player discards that card.")
        thoughtseize = discard_spell("thoughtseize", "Thoughtseize", "Target player reveals their hand. You choose a nonland card from it. That player discards that card. You lose 2 life.")
        others = [
            discard_spell("despise", "Despise", "Target opponent reveals their hand. You choose a creature or planeswalker card from it. That player discards that card."),
            discard_spell("ostracize", "Ostracize", "Target opponent reveals their hand. You choose a creature card from it. That player discards that card."),
            discard_spell("dreams", "Shattered Dreams", "Target opponent reveals their hand. You choose an artifact card from it. That player discards that card."),
        ]
        next_page = "https://api.scryfall.com/cards/search?page=2"
        with patch.object(application, "fetch_json", return_value={
            "data": others, "has_more": True, "next_page": next_page,
        }) as first, patch.object(application, "fetch_json_from_url", return_value={
            "data": [thoughtseize], "has_more": False,
        }) as later, patch.object(application.time, "sleep"):
            result = application.get_similar_cards(selected)
        self.assertEqual(result[0]["name"], "Thoughtseize")
        self.assertEqual(len(result), 3)
        later.assert_called_once_with(next_page)
        self.assertIn("o%3Adiscard", first.call_args.args[0])
        self.assertIn("cmc%3D1", first.call_args.args[0])
        allowed = application.card_hand_choice_types(selected)
        self.assertNotIn("creature", allowed)
        self.assertNotIn("land", allowed)
        self.assertIn("instant", allowed)

    def test_self_discard_is_not_targeted_hand_disruption(self):
        selected = card("loot", "Loot", oracle_text="Draw two cards, then discard a card.")
        self.assertEqual(application.card_function_tags(selected), {"draw_and_discard"})
        self.assertEqual(application.card_hand_choice_types(selected), set())

    def test_detail_loads_independently_and_recommendations_link_to_detail(self):
        with patch.object(application, "get_card", return_value=self.selected), \
             patch.object(application, "get_card_editions", return_value=[]), \
             patch.object(application, "find_saved_status", return_value=None), \
             patch.object(application, "load_collection", return_value=[]), \
             patch.object(application, "get_similar_cards", return_value=self.candidates) as recommend:
            client = application.app.test_client()
            response = client.get("/card/selected")
            self.assertEqual(response.status_code, 200)
            self.assertIn("También te podría interesar", response.get_data(as_text=True))
            recommend.assert_not_called()
            response = client.get("/card/selected/recommendations")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_data(as_text=True).count('class="similar-card"'), 3)
            self.assertIn('href="/card/0"', response.get_data(as_text=True))

    def test_service_failure_and_empty_results_have_fallbacks(self):
        with patch.object(application, "get_card", return_value=self.selected), \
             patch.object(application, "load_collection", return_value=[]), \
             patch.object(application, "get_similar_cards", side_effect=RuntimeError("Offline")):
            response = application.app.test_client().get("/card/selected/recommendations")
            self.assertEqual(response.status_code, 502)
            self.assertIn("No pudimos cargar", response.get_data(as_text=True))
        with patch.object(application, "get_card", return_value=self.selected), \
             patch.object(application, "load_collection", return_value=[]), \
             patch.object(application, "get_similar_cards", return_value=[]):
            response = application.app.test_client().get("/card/selected/recommendations")
            self.assertEqual(response.status_code, 200)
            self.assertIn("No encontramos", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
