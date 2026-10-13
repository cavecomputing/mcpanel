import json
import os
import shutil
from types import SimpleNamespace

import pytest

from mcpanel import accounts, config, files
from mcpanel.db import get_db

from .conftest import make_user, signed_in_client

SURVIVAL = {'name': 'Survival', 'type': 'PAPER', 'version': '1.21.4', 'java': 'java21', 'memory_gb': 4, 'eula': True}
SERVER_KEYS = {'id', 'name', 'status', 'type', 'version', 'java', 'memory_gb', 'cpus', 'memory_limit', 'port', 'address', 'created', 'started', 'game_version', 'progress', 'oom_killed'}


def create(client, **fields):
    return client.post('/api/servers', json={**SURVIVAL, **fields})


def grant(admin, member, *server_ids):
    """Let member use server_ids, through the users API as a platform admin would (admin only says
    which app)."""
    owner = accounts.user_named('owner') or make_user('owner', 'platform_admin')
    response = signed_in_client(admin.application, owner).put(f'/api/users/{member.user["id"]}', json={'role': 'member', 'servers': list(server_ids)})
    assert response.status_code == 200, response.get_json()


def stale_grant(member, server_id):
    """Let member use server_id, a server Docker no longer has, as a server removed outside the panel leaves."""
    with get_db() as conn:
        conn.execute('INSERT INTO server_access (user_id, server) VALUES (?, ?)', (member.user['id'], server_id))
        conn.commit()


def server_audit(data_dir):
    """(event, server, server_name, user) of each server_* line in audit.log."""
    lines = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    return [(line['event'], line['server'], line['server_name'], line['user'])
            for line in lines if line['event'].startswith('server_')]


def test_admin_creates_and_lists_servers(admin):
    response = create(admin)
    assert response.status_code == 201
    server = response.get_json()
    assert set(server) == SERVER_KEYS
    assert server == {**server, 'id': 'survival', 'name': 'Survival', 'status': 'stopped', 'type': 'PAPER',
                      'version': '1.21.4', 'java': 'java21', 'memory_gb': 4, 'cpus': None,
                      'memory_limit': 4 * 1024 ** 3, 'port': 25565,
                      'address': 'mc.example.com'}
    assert server['created'] > 0
    assert 'RCON' not in response.text and 'EULA' not in response.text and 'password' not in response.text.lower()

    creative = create(admin, name='Creative', type='VANILLA', version='LATEST', java='').get_json()
    assert (creative['id'], creative['port'], creative['address']) == ('creative', 25566, 'mc.example.com:25566')
    assert creative['java'] == 'java25'  # left to the panel, which matches it to the version
    response = admin.get('/api/servers')
    assert response.status_code == 200
    assert response.get_json() == {'servers': [creative, server]}  # by name
    assert 'RCON' not in response.text


def test_options(admin):
    assert admin.get('/api/servers/options').get_json() == {
        'types': ['VANILLA', 'PAPER', 'PURPUR', 'FABRIC', 'FORGE', 'NEOFORGE', 'QUILT'],
        'java': ['java25', 'java21', 'java17', 'java8'],
        'memory': {'min': 1, 'max': 32, 'default': 4},
        'cpus': {'min': 0.5},
    }


def test_admins_see_the_hosts_memory_and_cores(admin, member, anon, docker):
    assert admin.get('/api/servers/host').get_json() == {'memory': 32 * 1024 ** 3, 'cpus': 8}
    assert member.get('/api/servers/host').status_code == 403
    assert anon.get('/api/servers/host').status_code == 401
    docker.reachable = False
    assert admin.get('/api/servers/host').status_code == 503


def test_members_see_only_their_servers(admin, member):
    create(admin)
    create(admin, name='Creative')
    assert member.get('/api/servers').get_json() == {'servers': []}
    grant(admin, member, 'creative')
    stale_grant(member, 'gone')
    assert [server['id'] for server in member.get('/api/servers').get_json()['servers']] == ['creative']
    assert len(admin.get('/api/servers').get_json()['servers']) == 2


def test_a_member_runs_a_granted_server(admin, member, docker):
    create(admin)
    grant(admin, member, 'survival')
    started = member.post('/api/servers/survival/start')
    assert started.status_code == 200 and started.get_json()['status'] == 'starting'
    assert set(started.get_json()) == SERVER_KEYS
    assert member.post('/api/servers/survival/stop').get_json()['status'] == 'stopped'
    assert member.post('/api/servers/survival/start').get_json()['status'] == 'starting'
    assert member.post('/api/servers/survival/restart').get_json()['status'] == 'starting'
    assert [action for action, name, _ in docker.actions if name == 'mcpanel-survival'] \
        == ['connect', 'start', 'stop', 'start', 'restart']


@pytest.mark.parametrize('action', ['start', 'stop', 'restart'])
def test_servers_a_member_may_not_use_look_missing(admin, member, docker, action):
    create(admin)
    create(admin, name='Creative')
    grant(admin, member, 'creative')
    stale_grant(member, 'gone')
    actions = list(docker.actions)
    for server_id in ('survival', 'nope', 'gone'):
        response = member.post(f'/api/servers/{server_id}/{action}')
        assert response.status_code == 404 and response.get_json() == {'error': 'No such server'}
    assert docker.actions == actions


# same-site: a sibling app's page on the same domain, which gets the session cookie, SameSite=Lax or not.
@pytest.mark.parametrize('headers', [{'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                                     {'Origin': 'http://evil.example'}])
def test_writes_from_another_sites_page_are_refused(admin, docker, headers):
    create(admin)
    response = admin.post('/api/servers/survival/stop', headers=headers)
    assert response.status_code == 403 and response.get_json() == {'error': 'Cross-site request blocked'}
    assert docker.actions == []
    assert admin.post('/api/servers/survival/start', headers={'Sec-Fetch-Site': 'same-origin'}).status_code == 200
    assert admin.post('/api/servers/survival/stop', headers={'Origin': 'http://localhost'}).status_code == 200


@pytest.mark.parametrize('server_id', ['nope', 'Bad_Id', 'a' * 40])
def test_admins_get_404_for_a_missing_server(admin, server_id):
    response = admin.post(f'/api/servers/{server_id}/start')
    assert response.status_code == 404 and response.get_json() == {'error': 'No such server'}


def test_an_unknown_action_is_404(admin):
    create(admin)
    response = admin.post('/api/servers/survival/delete')
    assert response.status_code == 404 and 'error' in response.get_json()  # JSON, though no route matches


@pytest.mark.parametrize('method, path', [('post', '/api/servers'), ('delete', '/api/servers/survival')])
def test_members_get_403_on_admin_calls(admin, member, docker, method, path):
    create(admin)
    grant(admin, member, 'survival')
    docker.created.clear()
    response = getattr(member, method)(path, json=SURVIVAL if method == 'post' else None)
    assert response.status_code == 403 and response.get_json() == {'error': 'Admins only'}
    assert docker.created == [] and 'remove' not in [action for action, _, _ in docker.actions]


def test_members_see_the_form_options_too(member):
    assert member.get('/api/servers/options').status_code == 200


@pytest.mark.parametrize('method, path', [
    ('get', '/api/servers'), ('get', '/api/servers/options'), ('post', '/api/servers'),
    ('post', '/api/servers/survival/start'), ('post', '/api/servers/survival/stop'),
    ('post', '/api/servers/survival/restart'),
])
def test_anonymous_gets_401(anon, docker, method, path):
    response = getattr(anon, method)(path, json=SURVIVAL if path == '/api/servers' else None)
    assert response.status_code == 401 and response.get_json() == {'error': 'Sign in first'}
    assert docker.created == [] and docker.actions == []


@pytest.mark.parametrize('fields, error', [
    ({'name': None}, 'Give the server a name'),
    ({'name': '   '}, 'Give the server a name'),
    ({'name': 'x' * 41}, 'Server names can be at most 40 characters'),
    ({'type': 'SPIGOT'}, 'Unknown server type'),
    ({'version': 'newest'}, 'Version must be LATEST or a Minecraft release like 26.1'),
    ({'java': 'java99'}, 'Unknown Java version'),
    ({'memory_gb': 0}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'memory_gb': '4'}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'memory_gb': 33}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'eula': False}, 'Accept the Minecraft EULA to create a server'),
    ({'eula': None}, 'Accept the Minecraft EULA to create a server'),
])
def test_bad_fields_are_400_with_the_reason(admin, docker, fields, error):
    response = create(admin, **fields)
    assert response.status_code == 400 and response.get_json() == {'error': error}
    assert docker.created == []


@pytest.mark.parametrize('body, error', [
    ('', 'Give the server a name'),
    ('[1, 2]', 'Expected a JSON object'),
    ('{"name": ', 'Expected a JSON object'),
])
def test_a_bad_body_is_400(admin, docker, body, error):
    response = admin.post('/api/servers', data=body, content_type='application/json')
    assert response.status_code == 400 and response.get_json() == {'error': error}
    assert docker.created == []


def test_docker_unreachable_is_503(admin, member, docker):
    create(admin)
    grant(admin, member, 'survival')
    docker.reachable = False
    responses = [admin.get('/api/servers'), member.get('/api/servers'), create(admin, name='Creative')]
    responses += [client.post(f'/api/servers/survival/{action}')
                  for client in (admin, member) for action in ('start', 'stop', 'restart')]
    for response in responses:
        assert response.status_code == 503
        assert response.get_json()['error'] == 'Docker is unreachable: No such file or directory'


def test_no_free_port_is_409(admin, docker, monkeypatch):
    monkeypatch.setattr(config, 'PORT_RANGE', '25565-25566')
    create(admin)
    docker.add('web', ports={'80/tcp': 25566})  # another app holds the last one
    response = create(admin, name='Creative')
    assert response.status_code == 409
    assert response.get_json() == {'error': 'Every port in MCPANEL_PORT_RANGE is taken'}
    assert [server['id'] for server in admin.get('/api/servers').get_json()['servers']] == ['survival']


def test_a_port_held_outside_docker_is_409(admin, docker):
    docker.outside_ports = {25565}
    assert create(admin).status_code == 201  # the port is claimed at start, not at create
    response = admin.post('/api/servers/survival/start')
    assert response.status_code == 409
    assert response.get_json() == {'error': 'Port 25565 is in use by something outside Docker'}
    assert admin.get('/api/servers').get_json()['servers'][0]['status'] == 'stopped'


def test_every_server_action_is_audited(admin, member, data_dir):
    create(admin)
    grant(admin, member, 'survival')
    for action in ('stop', 'start', 'restart'):
        member.post(f'/api/servers/survival/{action}')
    admin.post('/api/servers/survival/stop')
    member.post('/api/servers/creative/start')  # refused: not audited
    create(admin, eula=False)  # refused: not audited
    assert server_audit(data_dir) == [
        ('server_created', 'survival', 'Survival', 'admin'),
        ('server_stopped', 'survival', 'Survival', 'member'),
        ('server_started', 'survival', 'Survival', 'member'),
        ('server_restarted', 'survival', 'Survival', 'member'),
        ('server_stopped', 'survival', 'Survival', 'admin'),
    ]


def test_disk_counts_the_folder_and_backups_never_through_a_link(admin, member, data_dir, tmp_path_factory, monkeypatch):
    create(admin)
    folder = config.SERVERS_DIR / 'survival'
    (folder / 'world' / 'region').mkdir(parents=True)
    (folder / 'world' / 'region' / 'r.0.0.mca').write_bytes(b'x' * 3000)
    (folder / 'server.properties').write_bytes(b'x' * 200)
    outside = tmp_path_factory.mktemp('host')
    (outside / 'big').write_bytes(b'x' * 10_000)
    os.symlink(outside, folder / 'host-folder')
    os.symlink(outside / 'big', folder / 'host-file')
    os.mkfifo(folder / 'pipe')
    backup = admin.post('/api/servers/survival/backups').get_json()
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: SimpleNamespace(free=123 if path == config.DATA_DIR else 0))
    assert admin.get('/api/servers/survival/disk').get_json() == {'folder': 3200, 'backups': backup['size'], 'free': 123}
    assert member.get('/api/servers/survival/disk').status_code == 404
    grant(admin, member, 'survival')
    assert member.get('/api/servers/survival/disk').status_code == 200


def test_a_folder_changed_while_it_is_measured_is_measured_as_it_is(admin, data_dir, monkeypatch):
    create(admin)
    (config.SERVERS_DIR / 'survival' / 'gone').mkdir()
    (config.SERVERS_DIR / 'survival' / 'kept').write_bytes(b'x' * 10)
    real_open = os.open

    def open_gone(path, *args, **kwargs):  # the folder deleted between its listing and its opening
        if path == 'gone':
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(files.os, 'open', open_gone)
    assert admin.get('/api/servers/survival/disk').get_json()['folder'] == 10


def test_stats_say_nothing_while_stopped_then_usage_and_players(admin, member, docker, rcon_server):
    create(admin)
    assert admin.get('/api/servers/survival/stats').get_json() == {'usage': None, 'players': None, 'max_players': None,
                                                                   'tps': None}
    assert rcon_server.commands == []
    admin.post('/api/servers/survival/start')
    rcon_server.answers['list'] = 'There are 1 of a max of 20 players online: Steve'
    rcon_server.answers['tps'] = 'TPS from last 1m, 5m, 15m: 19.7, 19.9, 20.0'
    assert admin.get('/api/servers/survival/stats').get_json() == {
        'usage': {'memory': int(2.5 * 1024 ** 3), 'memory_limit': 5 * 1024 ** 3, 'cpu': 400.0},
        'players': 1, 'max_players': 20, 'tps': 19.7}
    assert member.get('/api/servers/survival/stats').status_code == 404
