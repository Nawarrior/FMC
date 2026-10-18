import json
import sqlite3
import unittest
from unittest.mock import patch

import database
import decks
import tournaments

with patch('database.initialize_database'), patch('database.migrate_json_files_to_db'):
    import app as application


class TournamentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for target in (database, application):
            patcher = patch.object(target, 'get_connection', return_value=self.db)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize_database()
        self.users = [dict(user_id='admin', name='Admin', email='admin@example.test', role='adm'),
                      dict(user_id='player', name='Jugador FMC', email='private@example.test', role='usuario')]
        database.save_collection('users', self.users)
        database.save_collection('stores', [dict(shop_id='store', name='Tienda FMC', address='Calle 123')])
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()
        self.login()

    def login(self, index=0):
        with self.client.session_transaction() as session:
            session['user'] = self.users[index]
            session['_csrf_token'] = 'test'

    def fields(self, results=None, **extra):
        return dict(csrf_token='test', name='Copa FMC', event_date='2026-10-18', format='Modern',
                    location='Local independiente', results_json=json.dumps(results or []), **extra)

    def create(self, results=None, **extra):
        response = self.client.post('/tournaments/manage', data=self.fields(results, **extra))
        self.assertEqual(response.status_code, 302, response.data.decode())
        return tournaments.list_events()[0]

    def test_admin_guard_csrf_and_current_database_role(self):
        self.assertEqual(self.client.get('/tournaments/manage').status_code, 200)
        self.assertEqual(self.client.post('/tournaments/manage', data={}).status_code, 400)
        self.login(1)
        self.assertEqual(self.client.get('/tournaments/manage').status_code, 403)
        self.assertEqual(self.client.post('/tournaments/manage', data=self.fields()).status_code, 403)
        self.login()
        self.db.execute("UPDATE users SET role='adm' WHERE user_id='player'")
        self.db.execute("UPDATE users SET role='usuario' WHERE user_id='admin'")
        self.db.commit()
        self.assertEqual(self.client.get('/tournaments/manage').status_code, 403)

    def test_manual_players_venue_public_results_and_edit(self):
        event = self.create([dict(position=2, player_name='Invitado', deck_name='Burn'),
                             dict(position=1, player_name='<script>alert(1)</script>', deck_name='Control')])
        self.assertEqual(self.client.get('/tournaments/manage?edit=' + event['tournament_id']).status_code, 200)
        self.assertEqual([entry['position'] for entry in event['results']], [1, 2])
        self.assertEqual(len(database.load_collection('users')), 2)
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/tournaments/' + event['tournament_id'])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        self.assertNotIn(b'Editar torneo', response.data)
        self.assertEqual(self.client.get('/tournaments?format=Modern').status_code, 200)
        self.assertNotIn(b'Copa FMC', self.client.get('/tournaments?format=Pauper').data)
        self.login()
        data = self.fields(event['results'], tournament_id=event['tournament_id'], revision=event['revision'])
        data['name'] = 'Copa editada'
        self.assertEqual(self.client.post('/tournaments/manage', data=data).status_code, 302)
        self.assertEqual(tournaments.get_event(event['tournament_id'])['name'], 'Copa editada')
        self.assertEqual(self.client.post('/tournaments/manage', data=data).status_code, 400)

    def test_optional_associations_are_validated_and_linked(self):
        deck_id = decks.create_deck('player', dict(name='Deck registrado', format='Modern', description=''))
        bad = self.fields([dict(position=1, user_id='player', deck_id=deck_id)])
        self.assertEqual(self.client.post('/tournaments/manage', data=bad).status_code, 400)
        self.db.execute('UPDATE decks SET published=1 WHERE deck_id=?', (deck_id,))
        self.db.commit()
        fields = self.fields([dict(position=1, user_id='player', deck_id=deck_id)], store_id='store')
        fields['location'] = ''
        self.assertEqual(self.client.post('/tournaments/manage', data=fields).status_code, 302)
        event = tournaments.list_events()[0]
        self.assertEqual(event['location'], 'Calle 123')
        self.assertEqual(event['results'][0]['player_name'], 'Jugador FMC')
        page = self.client.get('/tournaments/' + event['tournament_id']).data
        for link in ('/user/player', '/stores/store', '/decks/public/' + deck_id):
            self.assertIn(link.encode(), page)
        self.assertNotIn(b'private@example.test', page)
        self.assertNotIn(b'private@example.test', self.client.get('/tournaments/manage').data)
        for entry in (dict(position=1, user_id='missing', deck_name='Burn'),
                      dict(position=1, player_name='Guest', deck_name='Burn', deck_id='missing')):
            self.assertEqual(self.client.post('/tournaments/manage', data=self.fields([entry])).status_code, 400)

    def test_standalone_deck_resolve_preserve_and_readonly(self):
        event = self.create([dict(position=1, player_name='Invitado', deck_name='Burn', deck_text='2 Lightning Bolt')])
        base = '/tournaments/' + event['tournament_id']
        admin = '/tournaments/manage/' + event['tournament_id']
        card = dict(id='bolt', name='Lightning Bolt', set='m11', collector_number='146', type_line='Instant',
                    prices={'usd': '1.00'}, game_changer=False,
                    image_uris={'normal': 'https://example.test/bolt.jpg', 'art_crop': 'https://example.test/art.jpg'})
        with patch('bulk_import.fetch_collection', return_value=[card]):
            response = self.client.post(admin + '/resolve', data=dict(csrf_token='test', revision=event['revision']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['pending'], 0)
        resolved = tournaments.get_event(event['tournament_id'])
        self.assertEqual(resolved['results'][0]['cards'][0]['status'], 'ready')
        parsed = tournaments.parse_event(self.fields(resolved['results']), resolved)
        self.assertEqual(parsed['results'][0]['cards'], resolved['results'][0]['cards'])
        with self.client.session_transaction() as session:
            session.clear()
        url = base + '/decks/' + resolved['results'][0]['entry_id']
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-readonly="true"', page.data)
        self.assertNotIn(b'id="deck-publish"', page.data)
        self.assertEqual(self.client.get(url + '/analysis').status_code, 200)
        self.login(1)
        self.assertEqual(self.client.post(admin + '/resolve', data=dict(csrf_token='test', revision=1)).status_code, 403)
        self.assertEqual(self.client.post(admin + '/delete', data=dict(csrf_token='test', revision=1)).status_code, 403)
        self.login()
        self.assertEqual(self.client.post(admin + '/delete', data=dict(csrf_token='test', revision=0)).status_code, 409)
        self.assertEqual(self.client.post(admin + '/delete', data=dict(csrf_token='test', revision=resolved['revision'])).status_code, 302)
        self.assertEqual(self.client.get(base).status_code, 404)

    def test_invalid_date_place_results_and_format(self):
        for key, value in (('event_date', 'bad'), ('location', ''), ('format', 'bad'), ('store_id', 'missing'),
                           ('results_json', '{}'), ('results_json', '[')):
            fields = self.fields()
            fields[key] = value
            self.assertEqual(self.client.post('/tournaments/manage', data=fields).status_code, 400)
        for entry in (dict(position=0, player_name='A', deck_name='D'), dict(position=1, deck_name='D'),
                      dict(position=1, player_name='A'),
                      dict(position=1, player_name='A', deck_name='D', deck_text='0 Lightning Bolt')):
            self.assertEqual(self.client.post('/tournaments/manage', data=self.fields([entry])).status_code, 400)
        self.assertEqual(tournaments.list_events(), [])


if __name__ == '__main__':
    unittest.main()
