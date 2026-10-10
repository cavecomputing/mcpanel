import json

from mcpanel import config

from .test_servers_api import create, grant


def write_lists(folder):
    (folder / 'whitelist.json').write_text(json.dumps([{'uuid': '1', 'name': 'steve'}, {'uuid': '2', 'name': 'Alex'}]))
    (folder / 'ops.json').write_text(json.dumps([{'uuid': '1', 'name': 'steve', 'level': 4}]))
    (folder / 'banned-players.json').write_text(json.dumps([{'uuid': '3', 'name': 'Griefer', 'reason': 'TNT'}]))


def test_a_stopped_server_shows_its_lists_and_nobody_online(admin, docker, rcon_server):
    create(admin)
    write_lists(config.SERVERS_DIR / 'survival')
    assert admin.get('/api/servers/survival/players').get_json() == {
        'online': None, 'max': None, 'whitelist': ['Alex', 'steve'], 'ops': ['steve'],
        'banned': [{'name': 'Griefer', 'reason': 'TNT'}]}
    assert rcon_server.commands == []


def test_a_running_server_says_who_is_online(admin, docker, rcon_server):
    create(admin)
    admin.post('/api/servers/survival/start')
    rcon_server.answers['list'] = 'There are 2 of a max of 20 players online: steve, Alex'
    data = admin.get('/api/servers/survival/players').get_json()
    assert (data['online'], data['max'], data['whitelist']) == (['steve', 'Alex'], 20, [])
    rcon_server.answers['list'] = 'There are 0/10 players online:'
    data = admin.get('/api/servers/survival/players').get_json()
    assert (data['online'], data['max']) == ([], 10)


def test_a_server_still_starting_or_odd_lists_say_nothing(admin, docker, rcon_server, monkeypatch):
    create(admin)
    admin.post('/api/servers/survival/start')
    rcon_server.answers['list'] = 'Unknown command'
    assert admin.get('/api/servers/survival/players').get_json()['online'] is None
    (config.SERVERS_DIR / 'survival' / 'ops.json').write_text('{not json')
    (config.SERVERS_DIR / 'survival' / 'whitelist.json').write_text('{"name": "x"}')
    rcon_server.listener.close()  # not listening yet
    data = admin.get('/api/servers/survival/players').get_json()
    assert (data['online'], data['ops'], data['whitelist']) == (None, [], [])


def test_members_see_only_their_servers_players(admin, member, docker):
    create(admin)
    assert member.get('/api/servers/survival/players').status_code == 404
    grant(admin, member, 'survival')
    assert member.get('/api/servers/survival/players').status_code == 200
