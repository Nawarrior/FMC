from io import BytesIO
import sqlite3
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement, tostring
import zipfile

import database
import bulk_import

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


def excel_fixture(rows):
    """Small OOXML input fixture, independent of the reader under test."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = Element("worksheet", xmlns=namespace)
    SubElement(sheet, "dimension", ref=f"A1:F{len(rows)}")
    data = SubElement(sheet, "sheetData")
    for index, values in enumerate(rows, 1):
        row = SubElement(data, "row", r=str(index))
        for column, value in enumerate(values):
            cell = SubElement(row, "c", r=f"{chr(65 + column)}{index}", t="inlineStr")
            if isinstance(value, str) and value.startswith("="):
                cell.attrib.pop("t")
                SubElement(cell, "f").text = value[1:]
            else:
                SubElement(SubElement(cell, "is"), "t").text = str(value)
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Cartas" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", tostring(sheet))
    return output.getvalue()


CARDS = [
    {"id": "duress", "name": "Duress", "set": "m21", "set_name": "Core Set 2021", "collector_number": "96", "rarity": "common", "image_uris": {"normal": "https://images.example.test/duress.jpg"}},
    {"id": "thoughtseize", "name": "Thoughtseize", "set": "ths", "set_name": "Theros", "collector_number": "107", "rarity": "rare"},
]


class BulkImportTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        connection = patch.object(database, "get_connection", return_value=self.db)
        connection.start()
        self.addCleanup(connection.stop)
        database.initialize_database()
        self.owner = {"user_id": "owner", "name": "Owner", "email": "owner@example.test", "role": "usuario", "country": "CL"}
        self.other = {"user_id": "other", "name": "Other", "email": "other@example.test", "role": "usuario", "country": "CL"}
        database.save_collection("users", [self.owner, self.other])
        self.client = application.app.test_client()
        application.app.config.update(TESTING=True)

    def login(self, user=None):
        with self.client.session_transaction() as session:
            session["user"] = user or self.owner
            session["_csrf_token"] = "token"

    def batch(self, text="4 Duress\n2 Thoughtseize"):
        rows = bulk_import.parse_upload(text.encode(), "cards.txt", "busco")
        return bulk_import.create_import("owner", rows)

    def resolve(self, batch_id):
        with patch.object(bulk_import, "fetch_collection", return_value=CARDS):
            return bulk_import.resolve_import(batch_id, "owner")

    def test_plain_text_quantities_names_commas_and_set_number(self):
        rows = bulk_import.parse_upload(b"4 Duress\n2x Thoughtseize\nThalia, Guardian of Thraben\n1 Duress (M21) 96", "cards.txt", "ofrezco")
        self.assertEqual([row["quantity"] for row in rows], [4, 2, 1, 1])
        self.assertEqual(rows[2]["name"], "Thalia, Guardian of Thraben")
        self.assertEqual((rows[3]["set"], rows[3]["number"]), ("m21", "96"))
        self.assertTrue(all(row["intent"] == "ofrezco" for row in rows))

    def test_excel_and_csv_headers_and_mixed_intents(self):
        excel = excel_fixture([["Nombre", "Cantidad", "Edicion", "Numero", "Nota", "Estado"], ["Duress", 20, "M21", "96", "En ingles", "Ofrezco"]])
        row = bulk_import.parse_upload(excel, "cards.xlsx", "busco")[0]
        self.assertEqual((row["name"], row["quantity"], row["intent"]), ("Duress", 20, "ofrezco"))
        csv = 'Nombre;Cantidad;Edición;Numero;Nota;Estado\nDuress;4;M21;96;Buscada;Busco\nThoughtseize;2;THS;107;;Ofrezco'.encode()
        rows = bulk_import.parse_upload(csv, "cards.csv", "busco")
        self.assertEqual([row["intent"] for row in rows], ["busco", "ofrezco"])

    def test_invalid_quantities_formula_and_missing_names_are_row_errors(self):
        rows = bulk_import.parse_rows([["Nombre", "Cantidad"], ["Duress", 0], ["Thoughtseize", 1.5], ["", 4], ["=SUM(1,2)", 1]], "busco")
        self.assertTrue(all(row["status"] == "error" for row in rows))
        data = excel_fixture([["Nombre", "Cantidad"], ["Duress", "=2+2"]])
        self.assertEqual(bulk_import.parse_upload(data, "cards.xlsx", "busco")[0]["status"], "error")

    def test_limits_and_unsupported_files(self):
        for data, name in [(b"a" * (bulk_import.MAX_BYTES + 1), "cards.txt"), (b"Duress\n" * 502, "cards.txt"), (b"old excel", "cards.xls"), (b"invalid", "cards.xlsx"), (b"", "cards.txt")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                bulk_import.parse_upload(data, name, "busco")

    def test_preview_does_not_save_and_missing_cards_are_reported(self):
        batch_id = self.batch("4 Duress\n1 No Such Card")
        batch = self.resolve(batch_id)
        self.assertEqual([row["status"] for row in batch["rows"]], ["ready", "error"])
        self.assertEqual(database.load_collection("saved_cards"), [])
        added = bulk_import.confirm_import(batch_id, self.owner, {0})
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["quantity"], 4)
        self.assertEqual(added[0]["user_id"], "owner")
        with self.assertRaises(ValueError):
            bulk_import.confirm_import(batch_id, self.owner, {0})
        self.assertEqual(len(database.load_collection("saved_cards")), 1)

    def test_duplicates_and_other_users_do_not_modify_existing_entries(self):
        batch_id = self.batch("4 Duress\n2 Duress")
        batch = self.resolve(batch_id)
        self.assertEqual([row["status"] for row in batch["rows"]], ["ready", "duplicate"])
        with self.assertRaises(ValueError):
            bulk_import.confirm_import(batch_id, self.other, {0})
        self.assertIsNone(bulk_import.get_import(batch_id, "other"))
        with self.assertRaises(ValueError):
            bulk_import.confirm_import(batch_id, self.owner, {1})
        bulk_import.confirm_import(batch_id, self.owner, {0})
        second = self.batch("3 Duress")
        self.assertEqual(self.resolve(second)["rows"][0]["status"], "duplicate")
        self.assertEqual(database.load_collection("saved_cards")[0]["quantity"], 4)

    def test_scryfall_failure_can_retry_and_resolves_in_chunks(self):
        batch_id = self.batch("\n".join("Duress" for _ in range(30)))
        with patch.object(bulk_import, "fetch_collection", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                bulk_import.resolve_import(batch_id, "owner")
        self.assertTrue(all(row["status"] == "pending" for row in bulk_import.get_import(batch_id, "owner")["rows"]))
        first = self.resolve(batch_id)
        self.assertEqual(sum(row["status"] == "pending" for row in first["rows"]), 5)
        with self.assertRaises(ValueError):
            bulk_import.confirm_import(batch_id, self.owner, {0})
        second = self.resolve(batch_id)
        self.assertEqual(sum(row["status"] == "pending" for row in second["rows"]), 0)

    def test_large_upload_has_a_readable_error(self):
        self.login()
        response = self.client.post("/saved/import", data={"csrf_token": "token", "intent": "busco", "file": (BytesIO(b"a" * (bulk_import.MAX_BYTES + 100000)), "cards.txt")})
        self.assertEqual(response.status_code, 413)
        self.assertIn("2 MB", response.get_data(as_text=True))

    def test_upload_resolve_confirm_routes_create_pending_notifications_without_smtp(self):
        database.save_collection("saved_cards", [{"card_id": "another-print", "name": "Duress", "intent": "ofrezco", "quantity": 1, "user_id": "other", "user_email": "other@example.test"}])
        self.login()
        response = self.client.post("/saved/import", data={"csrf_token": "token", "intent": "busco", "file": (BytesIO(b"4 Duress"), "cards.txt")})
        self.assertEqual(response.status_code, 302)
        review_url = response.headers["Location"]
        self.assertIn("/saved/import/", review_url)
        self.assertEqual(self.client.get(review_url).status_code, 200)
        with patch.object(bulk_import, "fetch_collection", return_value=CARDS):
            response = self.client.post(review_url + "/resolve", headers={"X-CSRF-Token": "token"})
        self.assertEqual(response.json["pending"], 0)
        with patch.object(application, "send_email") as smtp:
            self.assertEqual(self.client.post(review_url + "/confirm", data={"csrf_token": "token", "row": "0"}).status_code, 302)
            smtp.assert_not_called()
        self.assertEqual(len(database.load_card_notifications("other")), 1)
        self.assertEqual(len(database.load_collection("saved_cards")), 2)

    def test_routes_require_login_owner_and_csrf(self):
        self.assertEqual(self.client.get("/saved/import").status_code, 302)
        self.login()
        self.assertEqual(self.client.post("/saved/import", data={"cards_text": "Duress"}).status_code, 400)
        batch_id = self.batch()
        self.login(self.other)
        self.assertEqual(self.client.get(f"/saved/import/{batch_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/saved/import/{batch_id}/resolve", headers={"X-CSRF-Token": "token"}).status_code, 404)
        self.assertEqual(self.client.post(f"/saved/import/{batch_id}/confirm", data={"csrf_token": "token", "row": "0"}).status_code, 404)

    def test_large_quantity_can_be_edited_without_truncation(self):
        batch_id = self.batch("20 Duress")
        self.resolve(batch_id)
        bulk_import.confirm_import(batch_id, self.owner, {0})
        self.login()
        with patch.object(application, "get_card", return_value=CARDS[0]):
            response = self.client.post("/saved/0/update", data={"csrf_token": "token", "quantity": "120", "intent": "busco"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(database.load_collection("saved_cards")[0]["quantity"], 120)

    def test_saved_list_filters_and_paginates_before_any_scryfall_lookup(self):
        database.save_collection("saved_cards", [{"card_id": str(index), "name": f"Card {index}", "user_id": "owner", "user_email": "owner@example.test", "intent": "busco", "quantity": 1} for index in range(55)])
        self.login()
        with patch.object(application, "get_card", side_effect=AssertionError("No eager Scryfall requests")):
            response = self.client.get("/saved?page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Card 54", response.get_data(as_text=True))
        self.assertNotIn("Card 49", response.get_data(as_text=True))
        self.assertIn("quantity-54", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
