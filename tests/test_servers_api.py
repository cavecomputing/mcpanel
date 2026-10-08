import json

import pytest

from mcpanel import config

SURVIVAL = {'name': 'Survival', 'type': 'PAPER', 'version': '1.21.4', 'java': 'java21', 'heap_gb': 4, 'eula': True}
SERVER_KEYS = {'id', 'name', 'status', 'type', 'version', 'java', 'heap_gb', 'port', 'address', 'created'}


def create(client, **fields):
    return client.post('/api/servers', json={**SURVIVAL, **fields})


def grant(admin, member, *server_ids):
    """Let member use server_ids, through the users API as an admin would."""
    response = admin.put(f'/api/users/{member.user["id"]}', json={'role': 'member', 'servers': list(server_ids)})
    assert response.status_code == 200, response.get_json()


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
    assert server == {**server, 'id': 'survival', 'name': 'Survival', 'status': 'starting', 'type': 'PAPER',
                      'version': '1.21.4', 'java': 'java21', 'heap_gb': 4, 'port': 25565,
                      'address': 'mc.example.com'}
    assert server['created'] > 0
    assert 'RCON' not in response.text and 'EULA' not in response.text and 'password' not in response.text.lower()

    creative = create(admin, name='Creative', type='VANILLA', version='LATEST').get_json()
    assert (creative['id'], creative['port'], creative['address']) == ('creative', 25566, 'mc.example.com:25566')
    response = admin.get('/api/servers')
    assert response.status_code == 200
    assert response.get_json() == {'servers': [creative, server]}  # by name
    assert 'RCON' not in response.text


def test_options(admin):
    assert admin.get('/api/servers/options').get_json() == {
        'types': ['VANILLA', 'PAPER', 'PURPUR', 'FABRIC', 'FORGE', 'NEOFORGE', 'QUILT'],
        'java': ['java21', 'java17', 'java11', 'java8'],
        'heap': {'min': 1, 'max': 32, 'default': 4},
    }


def test_members_see_only_their_servers(admin, member):
    create(admin)
    create(admin, name='Creative')
    assert member.get('/api/servers').get_json() == {'servers': []}
    grant(admin, member, 'creative', 'gone')  # gone: a server Docker no longer has
    assert [server['id'] for server in member.get('/api/servers').get_json()['servers']] == ['creative']
    assert len(admin.get('/api/servers').get_json()['servers']) == 2


def test_a_member_runs_a_granted_server(admin, member, docker):
    create(admin)
    grant(admin, member, 'survival')
    stopped = member.post('/api/servers/survival/stop')
    assert stopped.status_code == 200 and stopped.get_json()['status'] == 'stopped'
    assert set(stopped.get_json()) == SERVER_KEYS
    assert member.post('/api/servers/survival/start').get_json()['status'] == 'starting'
    assert member.post('/api/servers/survival/restart').get_json()['status'] == 'starting'
    assert [action for action, name, _ in docker.actions if name == 'mcpanel-survival'] \
        == ['start', 'stop', 'start', 'restart']


@pytest.mark.parametrize('action', ['start', 'stop', 'restart'])
def test_servers_a_member_may_not_use_look_missing(admin, member, docker, action):
    create(admin)
    create(admin, name='Creative')
    grant(admin, member, 'creative', 'gone')
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
    assert [action for action, _, _ in docker.actions] == ['start']
    assert admin.post('/api/servers/survival/stop', headers={'Sec-Fetch-Site': 'same-origin'}).status_code == 200
    assert admin.post('/api/servers/survival/start', headers={'Origin': 'http://localhost'}).status_code == 200


@pytest.mark.parametrize('server_id', ['nope', 'Bad_Id', 'a' * 40])
def test_admins_get_404_for_a_missing_server(admin, server_id):
    response = admin.post(f'/api/servers/{server_id}/start')
    assert response.status_code == 404 and response.get_json() == {'error': 'No such server'}


def test_an_unknown_action_is_404(admin):
    create(admin)
    response = admin.post('/api/servers/survival/delete')
    assert response.status_code == 404 and 'error' in response.get_json()  # JSON, though no route matches


@pytest.mark.parametrize('method, path', [('get', '/api/servers/options'), ('post', '/api/servers')])
def test_members_get_403_on_admin_calls(member, docker, method, path):
    response = getattr(member, method)(path, json=SURVIVAL if method == 'post' else None)
    assert response.status_code == 403 and response.get_json() == {'error': 'Admins only'}
    assert docker.created == []


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
    ({'version': 'newest'}, 'Version must be LATEST or a Minecraft release like 1.21.4'),
    ({'java': 'java99'}, 'Unknown Java version'),
    ({'heap_gb': 0}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'heap_gb': '4'}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'heap_gb': 33}, 'Memory must be a whole number of GB from 1 to 32'),
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
    response = create(admin)
    assert response.status_code == 409
    assert response.get_json() == {'error': 'Port 25565 is in use by something outside Docker'}
    assert admin.get('/api/servers').get_json() == {'servers': []}


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
