import io
import json
import os
import urllib.error

from mcpanel import config, players
from mcpanel.db import get_db

from .test_servers_api import create, grant


def write_lists(folder):
    (folder / 'whitelist.json').write_text(json.dumps([{'uuid': '1', 'name': 'steve'}, {'uuid': '2', 'name': 'Alex'}]))
    (folder / 'ops.json').write_text(json.dumps([{'uuid': '1', 'name': 'steve', 'level': 4}]))
    (folder / 'banned-players.json').write_text(json.dumps([{'uuid': '3', 'name': 'Griefer', 'reason': 'TNT'}]))


def test_a_stopped_server_shows_its_lists_and_nobody_online(admin, docker, rcon_server):
    create(admin)
    write_lists(config.SERVERS_DIR / 'survival')
    assert admin.get('/api/servers/survival/players').get_json() == {
        'online': None, 'max': None, 'all': [], 'whitelist': ['Alex', 'steve'], 'ops': ['steve'],
        'banned': [{'name': 'Griefer', 'reason': 'TNT', 'created': None, 'by': ''}]}
    assert rcon_server.commands == []


def test_everyone_who_has_joined_is_listed_latest_first(admin, docker, rcon_server):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    write_lists(folder)
    (folder / 'server.properties').write_text('level-name=smp\n')
    (folder / 'usercache.json').write_text(json.dumps([{'uuid': 'aa-1', 'name': 'Notch'}]))
    playerdata = folder / 'smp' / 'playerdata'
    playerdata.mkdir(parents=True)
    for name, at in (('AA-1.dat', 1000), ('2.dat', 3000), ('2.dat_old', 4000), ('99.dat', 2000)):
        (playerdata / name).write_text('')
        os.utime(playerdata / name, (at, at))
    # 99 has no name anywhere, so it's left out
    assert admin.get('/api/servers/survival/players').get_json()['all'] == [
        {'name': 'Alex', 'last_on': 3000}, {'name': 'Notch', 'last_on': 1000}]

    admin.post('/api/servers/survival/start')
    rcon_server.answers['list'] = 'There are 2 of a max of 20 players online: newbie, alex'
    assert admin.get('/api/servers/survival/players').get_json()['all'][:2] == [
        {'name': 'newbie', 'last_on': None}, {'name': 'Alex', 'last_on': 3000}]


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


def change(client, command, name, reason=None):
    return client.post('/api/servers/survival/players', json={'command': command, 'name': name} | ({'reason': reason} if reason is not None else {}))


def test_a_stopped_servers_lists_change_in_its_files(admin, docker, monkeypatch):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    write_lists(folder)
    mojang = Mojang({'notch': {'id': '069a79f444e94726a5befca90e38aaf5', 'name': 'Notch'}, 'alex': {'id': '6ab4317889fd490597f60f67d9d76fd9', 'name': 'Alex'}})
    monkeypatch.setattr(players.urllib.request, 'urlopen', mojang)

    response = change(admin, 'whitelist add', 'notch')
    assert response.status_code == 200 and response.get_json() == {'response': 'Added Notch to the whitelist'}
    assert json.loads((folder / 'whitelist.json').read_text())[-1] == {'uuid': '069a79f4-44e9-4726-a5be-fca90e38aaf5', 'name': 'Notch'}
    assert change(admin, 'op', 'Notch').status_code == 200
    assert json.loads((folder / 'ops.json').read_text())[-1] == {
        'uuid': '069a79f4-44e9-4726-a5be-fca90e38aaf5', 'name': 'Notch', 'level': 4, 'bypassesPlayerLimit': False}
    assert change(admin, 'ban', 'notch', ' Burned the village ').status_code == 200
    banned = json.loads((folder / 'banned-players.json').read_text())[-1]
    assert (banned['name'], banned['expires'], banned['source'], banned['reason']) == ('Notch', 'forever', 'mcpanel', 'Burned the village')
    ban = admin.get('/api/servers/survival/players').get_json()['banned'][-1]
    assert (ban['name'], ban['by']) == ('Notch', 'admin') and ban['created'] > 0

    assert change(admin, 'whitelist remove', 'STEVE').get_json() == {'response': 'Removed STEVE from the whitelist'}
    assert change(admin, 'pardon', 'griefer').status_code == 200
    assert change(admin, 'deop', 'steve').status_code == 200
    assert change(admin, 'ban', 'Alex').status_code == 200
    data = admin.get('/api/servers/survival/players').get_json()
    assert (data['whitelist'], data['ops']) == (['Alex', 'Notch'], ['Notch'])
    assert [(ban['name'], ban['reason']) for ban in data['banned']] == [('Alex', 'Banned by an operator.'), ('Notch', 'Burned the village')]

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


def test_bans_say_who_made_them(admin, docker):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    (folder / 'banned-players.json').write_text(json.dumps([
        {'name': 'Griefer', 'created': '2026-10-12 21:14:00 +0200', 'source': 'Rcon', 'reason': 'TNT'},
        {'name': 'flyer', 'created': '2026-10-03 18:02:00 +0000', 'source': 'Steve', 'reason': 'Fly hacks'}]))
    griefer_banned = 1791832440  # 2026-10-12 19:14:00 UTC
    players.note_ban('survival', 'griefer', 'matt')  # now: long after, so another ban's
    assert [ban['by'] for ban in admin.get('/api/servers/survival/players').get_json()['banned']] == ['Steve', 'Rcon']
    with get_db() as conn:
        conn.execute('INSERT INTO bans VALUES (?, ?, ?, ?)', ('survival', 'griefer', 'matt', griefer_banned - 3))
        conn.commit()
    assert admin.get('/api/servers/survival/players').get_json()['banned'] == [
        {'name': 'flyer', 'reason': 'Fly hacks', 'created': 1791050520, 'by': 'Steve'},
        {'name': 'Griefer', 'reason': 'TNT', 'created': griefer_banned, 'by': 'matt'}]
    assert admin.delete('/api/servers/survival').status_code == 200
    with get_db() as conn:
        assert conn.execute('SELECT * FROM bans').fetchall() == []


def test_a_running_servers_lists_change_through_its_console(admin, docker, rcon_server):
    create(admin)
    admin.post('/api/servers/survival/start')
    rcon_server.answers['ban'] = 'Banned Griefer: Burned the village'
    response = change(admin, 'ban', 'Griefer', 'Burned the village')
    assert response.get_json() == {'response': 'Banned Griefer: Burned the village'}
    assert change(admin, 'op', 'Steve', 'ignored').status_code == 200
    assert change(admin, 'ban', 'Steve', '').status_code == 200
    assert rcon_server.commands[-3:] == ['ban Griefer Burned the village', 'op Steve', 'ban Steve']
    with get_db() as conn:
        assert [row['player'] for row in conn.execute('SELECT player FROM bans')] == ['Griefer', 'Steve']
    assert not (config.SERVERS_DIR / 'survival' / 'ops.json').exists()


def test_list_changes_need_good_names_and_reasons(admin, member, docker):
    create(admin)
    assert change(admin, 'kill', 'Steve').status_code == 400
    assert change(admin, 'op', 'Steve; rm').status_code == 400
    assert change(admin, 'ban', 'Steve', 'line\nbreak').status_code == 400
    assert change(admin, 'ban', 'Steve', 'x' * 201).status_code == 400
    assert change(admin, 'ban', 'Steve', 5).status_code == 400
    assert change(member, 'op', 'Steve').status_code == 404
    assert not (config.SERVERS_DIR / 'survival' / 'ops.json').exists()
