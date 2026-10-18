import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import database

with patch("database.initialize_database"), patch("database.migrate_json_files_to_db"):
    import app as application


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        for module in (database, application):
            patcher = patch.object(module, "get_connection", return_value=self.connection)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize_database()
        self.users = [
            {"user_id": role, "name": role, "email": role + "@example.test", "role": role}
            for role in ("adm", "usuario", "moderador", "tienda")
        ]
        database.save_collection("users", self.users)
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()

    def login(self, role):
        with self.client.session_transaction() as session:
            session["user"] = dict(next(u for u in self.users if u["user_id"] == role))
            session["_csrf_token"] = "test"

    def post(self, **data):
        return self.client.post("/news/manage", data={"csrf_token": "test", **data})

    def test_public_news_and_navigation(self):
        response = self.client.get("/news")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/news">Noticias', response.data)
        self.assertNotIn(b"Mantenedor Noticias", response.data)

    def test_only_admin_can_manage_even_with_forged_session_role(self):
        self.assertEqual(self.client.get("/news/manage").status_code, 403)
        for role in ("usuario", "moderador", "tienda"):
            self.login(role)
            with self.client.session_transaction() as session:
                session["user"]["role"] = "adm"
            self.assertEqual(self.client.get("/news/manage").status_code, 403)
            self.assertEqual(self.post(title="Test", body="Text").status_code, 403)
            self.assertEqual(self.client.post("/news/x/delete", data={"csrf_token": "test"}).status_code, 403)
        self.assertEqual(database.load_collection("news"), [])

    def test_admin_create_edit_delete_and_safe_rendering(self):
        self.login("adm")
        self.assertEqual(self.post(title="Novedad", body="<script>alert(1)</script>\nTexto").status_code, 302)
        news_id = database.load_collection("news")[0]["news_id"]
        response = self.client.get("/news")
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertIn(b"Novedad", response.data)
        self.assertEqual(self.client.get("/news/manage?edit=" + news_id).status_code, 200)
        self.assertEqual(self.post(news_id=news_id, title="Editada", body="Contenido").status_code, 302)
        self.assertEqual(database.load_collection("news")[0]["title"], "Editada")
        self.assertEqual(self.client.post("/news/" + news_id + "/delete", data={"csrf_token": "test"}).status_code, 302)
        self.assertEqual(database.load_collection("news"), [])

    def test_validation_csrf_and_missing_news(self):
        self.login("adm")
        self.assertEqual(self.client.post("/news/manage", data={"title": "x", "body": "x"}).status_code, 400)
        for title, body in ((" ", "x"), ("x", " "), ("x" * 201, "x"), ("x", "x" * 20001)):
            self.assertEqual(self.post(title=title, body=body).status_code, 400)
        self.assertEqual(self.post(news_id="missing", title="x", body="x").status_code, 404)
        self.assertEqual(self.client.get("/news/manage?edit=missing").status_code, 404)
        self.assertEqual(self.client.post("/news/missing/delete", data={"csrf_token": "test"}).status_code, 404)
        self.assertEqual(database.load_collection("news"), [])

    def test_demoted_admin_cannot_publish(self):
        self.login("adm")
        users = [dict(u) for u in self.users]
        users[0]["role"] = "usuario"
        users[1]["role"] = "adm"
        database.save_collection("users", users)
        self.assertEqual(self.post(title="x", body="x").status_code, 403)

    def structured_fields(self):
        return {
            "title": "Festival in a Box", "body": "Introducción", "category": "Secret Lair",
            "lead": "Bajada editorial", "author": "Redacción FMC", "published_on": "2026-10-18",
            "hero_url": "https://example.test/banner.webp", "hero_alt": "Contenido de la caja",
            "hero_caption": "Créditos oficiales", "summary_title": "Antes del lanzamiento",
            "summary_json": json.dumps([{"label": "Precio", "value": "US$249,99"}]),
            "source_url": "https://example.test/source", "rights": "Créditos y derechos",
            "cta_title": "Tu próxima carta", "cta_url": "https://example.test/search",
            "sections_json": json.dumps([
                {"type": "text", "title": "Contenido", "text": "Párrafo\nSegundo párrafo"},
                {"type": "highlights", "title": "Incluye", "items": [{"title": "Caja", "text": "24 sobres"}]},
                {"type": "image", "title": "Producto", "items": [{"image_url": "https://example.test/box.webp", "alt": "Caja"}]},
                {"type": "gallery", "title": "Cartas", "items": [{"title": "Regrowth", "image_url": "https://example.test/card.webp", "alt": "Carta Regrowth", "credit": "Ilustración acreditada"}]},
                {"type": "links", "title": "Recursos", "items": [{"title": "Fuente oficial", "url": "https://example.test/source"}]},
                {"type": "list", "title": "Promos", "items": [{"title": "Dance of the Dead", "text": "Promo foil"}]},
            ]),
        }

    def test_structured_article_roundtrip_preview_and_edit(self):
        self.login("adm")
        fields = self.structured_fields()
        response = self.client.post("/news/preview", data={"csrf_token": "test", **fields})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Vista previa", response.data)
        self.assertEqual(database.load_collection("news"), [])
        self.assertEqual(self.post(**fields).status_code, 302)
        stored = database.load_collection("news")[0]
        self.assertEqual(stored["content"]["summary_items"][0]["value"], "US$249,99")
        self.assertEqual(len(stored["content"]["sections"]), 6)
        response = self.client.get("/news/" + stored["news_id"])
        self.assertEqual(response.status_code, 200)
        for expected in (b"fmc-news-gallery", b"Regrowth", b"24 sobres", b"fmc-news-summary", b"Fuente oficial", "Créditos y derechos".encode()):
            self.assertIn(expected, response.data)
        self.assertIn(b"Secret Lair", self.client.get("/news/manage?edit=" + stored["news_id"]).data)
        fields["title"] = "Actualizada"
        self.assertEqual(self.post(news_id=stored["news_id"], **fields).status_code, 302)
        self.assertEqual(database.load_collection("news")[0]["content"]["sections"], stored["content"]["sections"])

    def test_preview_auth_and_csrf(self):
        self.assertEqual(self.client.post("/news/preview", data={"csrf_token": "test"}).status_code, 400)
        self.login("usuario")
        self.assertEqual(self.client.post("/news/preview", data={"csrf_token": "test", **self.structured_fields()}).status_code, 403)

    def test_rejects_unsafe_urls_and_malformed_blocks_preserving_editor(self):
        self.login("adm")
        for changes in (
            {"hero_url": "javascript:alert(1)"}, {"source_url": "data:text/html,test"},
            {"sections_json": '{bad'}, {"sections_json": '{}'},
            {"sections_json": json.dumps([{"type": "raw_html"}])},
            {"sections_json": json.dumps([{"type": "gallery", "items": [{"image_url": "https://example.test/x"}]}])},
            {"sections_json": json.dumps([{"type": "links", "items": [{"title": "Bad", "url": "javascript:alert(1)"}]}])},
            {"published_on": "2026-02-31"},
        ):
            fields = {**self.structured_fields(), **changes}
            response = self.post(**fields)
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"Bajada editorial", response.data)
        self.assertEqual(database.load_collection("news"), [])

    def test_legacy_news_detail_and_additive_schema(self):
        self.connection.execute("DROP TABLE news")
        self.connection.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, position INTEGER NOT NULL, news_id TEXT, title TEXT, body TEXT, created_at TEXT, updated_at TEXT)")
        self.connection.execute("INSERT INTO news VALUES (1, 0, 'legacy', 'Noticia anterior', 'Contenido anterior', '2026-10-18', '2026-10-18')")
        database.initialize_database()
        response = self.client.get("/news/legacy")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Contenido anterior", response.data)
        self.assertEqual(self.client.get("/news/missing").status_code, 404)

    def test_import_pasted_json_loads_editor_and_can_be_published(self):
        self.login("adm")
        payload = {"title": "Desde JSON", "body": "Introducción importada", "content": {
            "category": "Secret Lair", "summary_items": [{"label": "Precio", "value": "US$25"}],
            "sections": [{"type": "text", "title": "Detalle", "text": "Texto importado"}],
        }}
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": json.dumps(payload)})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Desde JSON', response.data)
        self.assertIn(b'Texto importado', response.data)
        self.assertIn(b'name="news_id" value=""', response.data)
        self.assertEqual(database.load_collection("news"), [])
        from news_content import import_json
        editing, content = import_json(json.dumps(payload))
        fields = {key: value for key, value in content.items() if key not in {"sections", "summary_items"}}
        fields.update(editing, sections_json=json.dumps(content["sections"]), summary_json=json.dumps(content["summary_items"]))
        self.assertEqual(self.post(**fields).status_code, 302)
        stored = database.load_collection("news")[0]
        self.assertEqual(stored["title"], "Desde JSON")
        self.assertEqual(stored["content"]["summary_items"][0]["value"], "US$25")

    def test_import_uploaded_template_and_utf8_bom(self):
        self.login("adm")
        raw = (Path(__file__).resolve().parents[1] / "static/examples/noticia.json").read_bytes()
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json_file": (io.BytesIO(b'\xef\xbb\xbf' + raw), "noticia.json")})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Galer', response.data)
        self.assertEqual(database.load_collection("news"), [])
        response = self.client.get("/static/examples/noticia.json")
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_import_rejects_malformed_and_unsafe_json(self):
        self.login("adm")
        payloads = ['{bad', '[]', '{}', json.dumps({"title": "x", "body": "x", "unknown": "x"}),
            json.dumps({"title": "x", "body": "x", "content": []}),
            json.dumps({"title": ["x"], "body": "x"}),
            json.dumps({"title": "x", "body": "x", "content": {"published_on": 123}}),
            json.dumps({"title": "x", "body": "x", "content": {"source_url": "javascript:alert(1)"}}),
            json.dumps({"title": "x", "body": "x", "content": {"sections": [{"type": []}]}}),
            json.dumps({"title": "x", "body": "x", "content": {"sections": "not a list"}}),
            json.dumps({"title": "x", "body": "x", "content": {"sections": [{"type": "gallery", "items": [{"image_url": "https://example.test/x"}]}]}}),
        ]
        for raw in payloads:
            response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": raw})
            self.assertEqual(response.status_code, 400, raw)
            self.assertIn(b'id="news-json-import" class="news-editor-panel" >', response.data)
        self.assertEqual(database.load_collection("news"), [])

    def test_import_file_errors_limits_and_auth(self):
        self.assertEqual(self.client.post("/news/import").status_code, 400)
        self.login("usuario")
        self.assertEqual(self.client.post("/news/import", data={"csrf_token": "test", "news_json": '{}'}).status_code, 403)
        self.login("adm")
        for raw, filename, pasted in ((b'{}', 'noticia.txt', ''), (b'\xff\xfe', 'noticia.json', ''), (b'{}', 'noticia.json', '{}')):
            response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": pasted, "news_json_file": (io.BytesIO(raw), filename)})
            self.assertEqual(response.status_code, 400)
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json_file": (io.BytesIO(b'x' * (1024 * 1024 + 1)), "grande.json")})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": 'x' * (2 * 1024 * 1024)})
        self.assertEqual(response.status_code, 413)
        self.assertIn(b'1 MB', response.data)
        self.assertEqual(database.load_collection("news"), [])

    def editorial_payload(self):
        fixture = Path(__file__).resolve().parent / "fixtures/news_article_v1.json"
        return json.loads(fixture.read_text(encoding="utf-8"))

    def test_import_exact_editorial_json_from_user_and_publish(self):
        from news_content import import_json
        payload = self.editorial_payload()
        editing, content = import_json(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(editing["title"], payload["title"])
        self.assertEqual(content["lead"], payload["summary"])
        self.assertEqual(content["published_on"], "2026-10-18")
        self.assertEqual(content["hero_url"], payload["banner"]["url"])
        self.assertIn(payload["banner"]["rights_notice"], content["hero_caption"])
        self.assertEqual(content["summary_items"][1]["value"], "US$249,99")
        galleries = [section for section in content["sections"] if section["type"] == "gallery"]
        self.assertEqual([len(section["items"]) for section in galleries], [4, 4])
        self.assertEqual(galleries[0]["items"][0]["title"], "Parallax Tide")
        self.assertEqual(galleries[1]["items"][-1]["credit"], payload["content"][4]["blocks"][1]["items"][-1]["credit"])
        for value in payload["rights"].values():
            if value:
                self.assertIn(value, content["rights"])
        self.assertEqual(content["cta_url"], payload["call_to_action"]["url"])
        self.login("adm")
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": json.dumps(payload)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.load_collection("news"), [])
        fields = {key: value for key, value in content.items() if key not in {"sections", "summary_items"}}
        fields.update(editing, sections_json=json.dumps(content["sections"]), summary_json=json.dumps(content["summary_items"]))
        response = self.client.post("/news/preview", data={"csrf_token": "test", **fields})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Auspicious Aquarium", response.data)
        self.assertEqual(response.data.count(b'<img src="https://media.wizards.com/'), 10)
        self.assertEqual(database.load_collection("news"), [])
        self.assertEqual(self.post(**fields).status_code, 302)
        self.assertEqual(database.load_collection("news")[0]["content"], content)

    def test_import_editorial_file_and_reject_invalid_editorial_blocks(self):
        from news_content import import_json
        self.login("adm")
        payload = self.editorial_payload()
        raw = json.dumps(payload, ensure_ascii=False).encode()
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json_file": (io.BytesIO(raw), "editorial.json")})
        self.assertEqual(response.status_code, 200)
        mutations = [
            ("schema_version", "2.0"), ("sources", "invalid"), ("content", [{}]),
            ("content", [{"type": "section", "blocks": [{"type": "script"}]}]),
            ("content", [{"type": "section", "blocks": [{"type": "list", "items": [None]}]}]),
            ("banner", {"source_id": "unknown"}),
            ("banner", {"url": "javascript:alert(1)", "alt": "Bad"}),
            ("key_facts", {"purchase_limit_per_customer": "two"}),
            ("key_facts", {"announced_price": {"amount": "NaN", "currency": "USD"}}),
            ("videos", [{"url": "https://example.test/video"}]),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    import_json(json.dumps({**payload, key: value}))
        self.assertEqual(database.load_collection("news"), [])

    def test_exact_editorial_video_json_import_preview_and_publish(self):
        from news_content import import_json
        raw = (Path(__file__).resolve().parent / "fixtures/news_article_video_v1.json").read_text(encoding="utf-8")
        payload = json.loads(raw)
        editing, content = import_json(raw)
        video_sections = [section for section in content["sections"] if section["type"] == "video"]
        self.assertEqual(len(video_sections), 1)
        video = video_sections[0]["items"][0]
        self.assertEqual(video["embed_url"], "https://www.youtube-nocookie.com/embed/aE02ACWjIJ8?rel=0")
        self.assertEqual(video["title"], payload["videos"][0]["title"])
        self.assertIn(payload["videos"][0]["rights_notice"], video["credit"])
        facts = {item["label"]: item["value"] for item in content["summary_items"]}
        self.assertEqual(facts["Plataforma"], "MTG Arena")
        self.assertEqual(facts["Enfrentamiento"], "Forrest vs. Eric")
        self.login("adm")
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": raw})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.load_collection("news"), [])
        fields = {key: value for key, value in content.items() if key not in {"sections", "summary_items"}}
        fields.update(editing, sections_json=json.dumps(content["sections"]), summary_json=json.dumps(content["summary_items"]))
        response = self.client.post("/news/preview", data={"csrf_token": "test", **fields})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<iframe src="https://www.youtube-nocookie.com/embed/aE02ACWjIJ8?rel=0"', response.data)
        self.assertNotIn(b'autoplay=', response.data)
        self.assertEqual(self.post(**fields).status_code, 302)
        saved = database.load_collection("news")[0]
        self.assertEqual(saved["content"], content)
        response = self.client.get("/news/" + saved["news_id"])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<iframe', response.data)

    def test_video_urls_are_validated_and_embeds_are_generated(self):
        from news_content import youtube_embed_url, import_json
        expected = "https://www.youtube-nocookie.com/embed/aE02ACWjIJ8?rel=0"
        for url in ("https://youtu.be/aE02ACWjIJ8", "https://www.youtube.com/watch?v=aE02ACWjIJ8&t=10", "https://www.youtube.com/shorts/aE02ACWjIJ8", "https://www.youtube-nocookie.com/embed/aE02ACWjIJ8?autoplay=1"):
            self.assertEqual(youtube_embed_url(url), expected)
        for url in ("https://evil.test/embed/aE02ACWjIJ8", "https://www.youtube.com.evil.test/watch?v=aE02ACWjIJ8", "javascript:alert(1)", "https://www.youtube.com/watch?v=bad", "https://www.youtube.com/watch?v=aE02ACWjIJ8&v=other"):
            with self.assertRaises(ValueError):
                youtube_embed_url(url)
        payload = json.loads((Path(__file__).resolve().parent / "fixtures/news_article_video_v1.json").read_text(encoding="utf-8"))
        for changes in ({"embed_url": "https://evil.test/embed/aE02ACWjIJ8"}, {"video_id": "bE02ACWjIJ8"}, {"provider": "other"}):
            mutated = {**payload, "videos": [{**payload["videos"][0], **changes}]}
            with self.assertRaises(ValueError):
                import_json(json.dumps(mutated))

    def test_exact_twitch_article_import_preview_and_dynamic_parent(self):
        from news_content import import_json
        raw = (Path(__file__).resolve().parent / "fixtures/news_article_twitch_v1.json").read_text(encoding="utf-8")
        editing, content = import_json(raw)
        videos = next(section["items"] for section in content["sections"] if section["type"] == "video")
        self.assertEqual([item["provider"] for item in videos], ["youtube", "twitch"])
        self.assertEqual(videos[1]["video_id"], "BovineHeartlessMosquitoWow-u_N0IBFc03QCIzpy")
        self.login("adm")
        response = self.client.post("/news/import", data={"csrf_token": "test", "news_json": raw})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(database.load_collection("news"), [])
        fields = {key: value for key, value in content.items() if key not in {"sections", "summary_items"}}
        fields.update(editing, sections_json=json.dumps(content["sections"]), summary_json=json.dumps(content["summary_items"]))
        response = self.client.post("/news/preview", data={"csrf_token": "test", **fields})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="https://clips.twitch.tv/embed?clip=BovineHeartlessMosquitoWow-u_N0IBFc03QCIzpy&amp;parent=localhost&amp;autoplay=false"', response.data)
        self.assertIn(b'Ver en Twitch', response.data)
        self.assertIn(b'Ver en YouTube', response.data)
        self.assertEqual(self.post(**fields).status_code, 302)
        news_id = database.load_collection("news")[0]["news_id"]
        for origin, parent in (("http://127.0.0.1:5000", "127.0.0.1"), ("https://nawarrior.pythonanywhere.com", "nawarrior.pythonanywhere.com"), ("https://example.test", "example.test")):
            response = self.client.get("/news/" + news_id, base_url=origin)
            self.assertEqual(response.status_code, 200)
            self.assertIn(('&amp;parent=' + parent + '&amp;autoplay=false').encode(), response.data)

    def test_twitch_validation_clips_vods_and_embeds(self):
        from news_content import video_details, import_json
        slug = "BovineHeartlessMosquitoWow-u_N0IBFc03QCIzpy"
        for url in ("https://clips.twitch.tv/" + slug, "https://www.twitch.tv/magic/clip/" + slug,
                    "https://clips.twitch.tv/embed?clip=" + slug + "&parent=imported.test&autoplay=true"):
            details = video_details(url, parent="actual.test")
            self.assertEqual(details["video_id"], slug)
            self.assertEqual(details["provider"], "twitch")
            self.assertIn("parent=actual.test&autoplay=false", details["embed_url"])
            self.assertNotIn("imported.test", details["embed_url"])
        for url in ("https://www.twitch.tv/videos/123456", "https://player.twitch.tv/?video=v123456&parent=old.test"):
            self.assertEqual(video_details(url)["embed_url"], "https://player.twitch.tv/?video=v123456&parent=localhost&autoplay=false")
        for url in ("https://clips.twitch.tv.evil.test/" + slug, "https://evil.test/embed?clip=" + slug,
                    "https://clips.twitch.tv/embed?clip=a&clip=b", "https://clips.twitch.tv/", "https://www.twitch.tv/videos/abc", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                video_details(url)
        payload = json.loads((Path(__file__).resolve().parent / "fixtures/news_article_twitch_v1.json").read_text(encoding="utf-8"))
        for changed in ({"provider": "youtube"}, {"video_id": "another"}, {"embed_url": "https://evil.test/embed"}):
            modified = {**payload, "videos": [{**payload["videos"][1], **changed}]}
            with self.assertRaises(ValueError):
                import_json(json.dumps(modified))


if __name__ == "__main__":
    unittest.main()
