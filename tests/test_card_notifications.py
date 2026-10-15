import sqlite3
import unittest
from unittest.mock import patch

import database

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


class CardNotificationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection_patch = patch.object(database, "get_connection", return_value=self.connection)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        database.initialize_database()
        self.users = [
            {"user_id": key, "name": key.title(), "email": key + "@example.test", "role": "usuario", "country": "CL"}
            for key in ("actor", "seller", "buyer", "other")
        ]
        database.save_collection("users", self.users)
        self.actor = self.users[0]
        self.card = {"id": "edition-new", "name": "Duress", "set_name": "Test set"}
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()

    def publication(self, owner, intent, **values):
        return {
            "card_id": "edition-old", "name": "Duress", "quantity": 1,
            "intent": intent, "transaction_completed": False,
            "user_id": owner, "user_email": owner + "@example.test", "user_name": owner.title(),
            **values,
        }

    def login(self, key="actor"):
        user = next(user for user in self.users if user["user_id"] == key)
        with self.client.session_transaction() as session:
            session["user"] = user
            session["_csrf_token"] = "test-token"

    def test_search_notifies_only_other_active_offering_owners_once(self):
        database.save_collection("saved_cards", [
            self.publication("seller", "ofrezco"),
            self.publication("seller", "ofrezco", card_id="another-edition", name=" DURESS "),
            self.publication("actor", "ofrezco"),
            self.publication("buyer", "busco"),
            self.publication("other", "ofrezco", transaction_completed=True),
            self.publication("other", "ofrezco", quantity=0),
            self.publication("other", "ofrezco", name="Thoughtseize"),
        ])
        self.assertEqual(application.notify_card_matches(self.card, "busco", self.actor), 1)
        notice = database.load_card_notifications("seller")[0]
        self.assertEqual(notice["kind"], "searched")
        self.assertEqual(notice["actor_user_id"], "actor")
        self.assertEqual(notice["card_id"], "edition-new")
        self.assertEqual(application.notify_card_matches(self.card, "busco", self.actor), 0)
        self.assertEqual(database.load_card_notifications("actor"), [])
        self.assertEqual(database.load_card_notifications("buyer"), [])
        self.assertEqual(database.load_card_notifications("other"), [])

    def test_offer_notifies_wanted_list_not_other_sellers(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco"), self.publication("buyer", "busco")])
        self.assertEqual(application.notify_card_matches(self.card, "ofrezco", self.actor), 1)
        self.assertEqual(database.load_card_notifications("buyer")[0]["kind"], "offered")
        self.assertEqual(database.load_card_notifications("seller"), [])

    def test_legacy_email_owner_and_self_email_are_resolved(self):
        database.save_collection("saved_cards", [
            self.publication("seller", "ofrezco", user_id=""),
            self.publication("actor", "ofrezco", user_id="legacy-id"),
        ])
        self.assertEqual(application.notify_card_matches(self.card, "busco", self.actor), 1)
        self.assertEqual(database.count_unread_card_notifications("seller"), 1)

    def test_anonymous_search_does_not_notify(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco")])
        with patch.object(application, "search_cards", return_value=[self.card]):
            self.assertEqual(self.client.get("/?q=Duress").status_code, 302)
        self.assertEqual(database.load_card_notifications("seller"), [])

    def test_exact_search_creates_persistent_notice_and_deduplicates(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco")])
        self.login()
        with patch.object(application, "search_cards", return_value=[self.card]):
            for _ in range(2):
                self.assertEqual(self.client.get("/?q=Duress").status_code, 302)
        database.initialize_database()
        self.assertEqual(database.count_unread_card_notifications("seller"), 1)

    def test_broad_search_waits_for_selection(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco")])
        self.login()
        with patch.object(application, "search_cards", return_value=[self.card, {"id": "other-card", "name": "Other card"}]):
            response = self.client.get("/?q=t%3Asorcery")
        self.assertEqual(response.status_code, 200)
        self.assertIn("from_search=1", response.get_data(as_text=True))
        self.assertEqual(database.count_unread_card_notifications("seller"), 0)
        with patch.object(application, "get_card", return_value=self.card), \
             patch.object(application, "get_card_editions", return_value=[]):
            self.assertEqual(self.client.get("/card/edition-new?from_search=1").status_code, 200)
        self.assertEqual(database.count_unread_card_notifications("seller"), 1)

    def test_saving_offer_and_wanted_entry_notifies_opposite_owners(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco"), self.publication("buyer", "busco")])
        self.login()
        with patch.object(application, "get_card", return_value=self.card):
            for intent in ("busco", "ofrezco"):
                response = self.client.post("/card/edition-new/save", data={"csrf_token": "test-token", "intent": intent})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(database.count_unread_card_notifications("seller"), 1)
        self.assertEqual(database.count_unread_card_notifications("buyer"), 1)

    def test_editing_intent_and_reactivating_offer_notify_buyers(self):
        database.save_collection("saved_cards", [self.publication("actor", "busco"), self.publication("buyer", "busco")])
        self.login()
        with patch.object(application, "get_card", return_value=self.card):
            response = self.client.post("/saved/0/update", data={"csrf_token": "test-token", "intent": "ofrezco"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(database.count_unread_card_notifications("buyer"), 1)
        self.client.post("/saved/0/transaction", data={"csrf_token": "test-token"})
        self.client.post("/saved/0/transaction", data={"csrf_token": "test-token"})
        self.assertEqual(database.count_unread_card_notifications("buyer"), 1)

    def test_notification_page_is_private_and_marks_only_own_notices_read(self):
        database.save_collection("saved_cards", [self.publication("seller", "ofrezco"), self.publication("buyer", "busco")])
        application.notify_card_matches(self.card, "busco", self.actor)
        application.notify_card_matches(self.card, "ofrezco", self.actor)
        self.assertEqual(self.client.get("/notifications").status_code, 302)
        self.login("seller")
        response = self.client.get("/notifications")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Duress", html)
        self.assertIn("Actor", html)
        self.assertIn("está buscando", html)
        self.assertNotIn("ofrece una carta", html)
        self.assertEqual(database.count_unread_card_notifications("seller"), 0)
        self.assertEqual(database.count_unread_card_notifications("buyer"), 1)


if __name__ == "__main__":
    unittest.main()
