import io
import json
import urllib.error

from mcpanel import config, players

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


class Mojang:
    """Stands in for urllib.request.urlopen to Mojang's API: the accounts it knows, by lowercase name."""

    def __init__(self, accounts, fail=None):
        self.accounts, self.fail, self.asked = accounts, fail, []

    def __call__(self, url, timeout):
        self.asked.append(url)
        if self.fail:
            raise self.fail
        account = self.accounts.get(url.rsplit('/', 1)[1].lower())
        if not account:
            raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
        return Answer(account)


class Answer(io.BytesIO):
    status = 200

    def __init__(self, account):
        super().__init__(json.dumps(account).encode())


def change(client, command, name):
    return client.post('/api/servers/survival/players', json={'command': command, 'name': name})


def test_a_stopped_servers_lists_change_in_its_files(admin, docker, monkeypatch):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    write_lists(folder)
    mojang = Mojang({'notch': {'id': '069a79f444e94726a5befca90e38aaf5', 'name': 'Notch'}})
    monkeypatch.setattr(players.urllib.request, 'urlopen', mojang)

    response = change(admin, 'whitelist add', 'notch')
    assert response.status_code == 200 and response.get_json() == {'response': 'Added Notch to the whitelist'}
    assert json.loads((folder / 'whitelist.json').read_text())[-1] == {'uuid': '069a79f4-44e9-4726-a5be-fca90e38aaf5', 'name': 'Notch'}
    assert change(admin, 'op', 'Notch').status_code == 200
    assert json.loads((folder / 'ops.json').read_text())[-1] == {
        'uuid': '069a79f4-44e9-4726-a5be-fca90e38aaf5', 'name': 'Notch', 'level': 4, 'bypassesPlayerLimit': False}
    assert change(admin, 'ban', 'notch').status_code == 200
    banned = json.loads((folder / 'banned-players.json').read_text())[-1]
    assert (banned['name'], banned['expires'], banned['source']) == ('Notch', 'forever', 'mcpanel')

    assert change(admin, 'whitelist remove', 'STEVE').get_json() == {'response': 'Removed STEVE from the whitelist'}
    assert change(admin, 'pardon', 'griefer').status_code == 200
    assert change(admin, 'deop', 'steve').status_code == 200
    assert admin.get('/api/servers/survival/players').get_json() | {'online': None} == {
        'online': None, 'max': None, 'whitelist': ['Alex', 'Notch'], 'ops': ['Notch'], 'banned': [{'name': 'Notch', 'reason': 'Banned by an operator.'}]}

    assert change(admin, 'whitelist add', 'nobody').status_code == 404
    monkeypatch.setattr(players.urllib.request, 'urlopen', Mojang({}, fail=urllib.error.URLError('timed out')))
    assert change(admin, 'whitelist add', 'notch').status_code == 502


def test_an_offline_server_gives_players_offline_uuids(admin, docker, monkeypatch):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    (folder / 'server.properties').write_text('online-mode=false\n')
    monkeypatch.setattr(players.urllib.request, 'urlopen', Mojang({}, fail=AssertionError('asked Mojang')))
    assert change(admin, 'whitelist add', 'Steve').status_code == 200
    # As Java's UUID.nameUUIDFromBytes("OfflinePlayer:Steve") makes it.
    assert json.loads((folder / 'whitelist.json').read_text()) == [{'uuid': '5627dd98-e6be-3c21-b8a8-e92344183641', 'name': 'Steve'}]


def test_list_files_change_only_while_stopped_and_with_good_names(admin, member, docker, rcon_server):
    create(admin)
    assert change(admin, 'kill', 'Steve').status_code == 400
    assert change(admin, 'op', 'Steve; rm').status_code == 400
    assert change(member, 'op', 'Steve').status_code == 404
    admin.post('/api/servers/survival/start')
    assert change(admin, 'op', 'Steve').status_code == 409
    assert not (config.SERVERS_DIR / 'survival' / 'ops.json').exists()
