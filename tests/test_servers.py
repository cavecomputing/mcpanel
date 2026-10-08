import json
import logging
import os
import time

import pytest
from docker.errors import APIError
from werkzeug.exceptions import HTTPException

from mcpanel import config, servers

REAL_DOCKER_CLIENT = servers.docker_client  # before the docker fixture swaps in the fake
GIB = 1024 ** 3

pytestmark = pytest.mark.usefixtures('data_dir', 'docker')


def refused(call, *args, **kwargs):
    """The (status, message) a call aborts with."""
    with pytest.raises(HTTPException) as error:
        call(*args, **kwargs)
    return error.value.code, error.value.description


def create(name='Survival', type='PAPER', version='1.21.4', java='java21', heap_gb=4, eula=True):
    return servers.create_server(name, type, version, java, heap_gb, eula)


def test_container_spec_is_the_image_one_mount_and_nothing_privileged(docker, data_dir):
    spec = servers.container_spec('survival', 'Survival', 'PAPER', '1.21.4', 'java21', 4, 25565)
    assert set(spec) == {'image', 'name', 'labels', 'environment', 'ports', 'volumes', 'network',
                         'restart_policy', 'stop_timeout', 'mem_limit', 'log_config'}
    assert spec['image'] == 'itzg/minecraft-server:java21'
    assert spec['name'] == 'mcpanel-survival'
    assert spec['labels'] == {'mcpanel.server': 'survival', 'mcpanel.name': 'Survival'}
    assert spec['volumes'] == {str(data_dir / 'servers' / 'survival'): {'bind': '/data', 'mode': 'rw'}}
    assert spec['ports'] == {'25565/tcp': 25565}
    assert spec['network'] == 'mcpanel'
    assert spec['restart_policy'] == {'Name': 'unless-stopped'}
    assert spec['stop_timeout'] == 60
    assert spec['mem_limit'] == 5 * GIB
    assert spec['log_config'] == {'type': 'json-file', 'config': {'max-size': '10m', 'max-file': '3'}}
    env = spec['environment']
    assert set(env) == {'EULA', 'TYPE', 'VERSION', 'MEMORY', 'UID', 'GID', 'RCON_PASSWORD'}
    assert {key: env[key] for key in ('EULA', 'TYPE', 'VERSION', 'MEMORY', 'UID', 'GID')} == {
        'EULA': 'TRUE', 'TYPE': 'PAPER', 'VERSION': '1.21.4', 'MEMORY': '4G',
        'UID': str(os.getuid()), 'GID': str(os.getgid())}
    # What the daemon is asked for, after the SDK's translation: nothing beyond these.
    create()
    [attrs] = docker.inspect.values()
    assert set(attrs['HostConfig']) == {'Binds', 'PortBindings', 'NetworkMode', 'RestartPolicy', 'Memory', 'LogConfig'}
    assert attrs['HostConfig']['Binds'] == [f'{data_dir}/servers/survival:/data:rw']
    assert attrs['Config']['StopTimeout'] == 60  # so Docker waits that long however the server is stopped


@pytest.mark.parametrize('heap_gb, limit', [(1, 2 * GIB), (4, 5 * GIB), (6, 7.5 * GIB), (32, 40 * GIB)])
def test_memory_limit_is_the_heap_plus_headroom(heap_gb, limit):
    assert servers.container_spec('x', 'X', 'VANILLA', 'LATEST', 'java21', heap_gb, 25565)['mem_limit'] == limit


def test_rcon_password_is_random_and_never_shown(docker, caplog):
    caplog.set_level(logging.INFO)
    shown = [create('One'), create('Two'), servers.list_servers(), servers.stop_server('one')]
    passwords = [spec['environment']['RCON_PASSWORD'] for spec in docker.created]
    assert len(set(passwords)) == 2 and all(len(password) >= 32 for password in passwords)
    for password in passwords:
        assert password not in json.dumps(shown)
        assert not any(password in str(vars(record)) for record in caplog.records)


@pytest.mark.parametrize('server_id', ['../x', 'A', '', '-x', 'x-', 'x' * 33, 'a_b', 'a\n', None, 5])
def test_check_id_refuses(server_id):
    assert refused(servers.check_id, server_id) == (404, 'No such server')


@pytest.mark.parametrize('server_id', ['a', '0', 'survival-2', 'x' * 32])
def test_check_id_accepts(server_id):
    assert servers.check_id(server_id) == server_id


NAME = 'Give the server a name'
VERSION = 'Version must be LATEST or a Minecraft release like 1.21.4'
MEMORY = 'Memory must be a whole number of GB from 1 to 32'
EULA = 'Accept the Minecraft EULA to create a server'


@pytest.mark.parametrize('field, value, message', [
    ('name', '', NAME), ('name', '   ', NAME), ('name', None, NAME),
    ('name', 'x' * 41, 'Server names can be at most 40 characters'),
    ('name', 'a\nb', "Server names can't contain control characters"),
    ('type', 'paper', 'Unknown server type'), ('type', 'SPIGOT', 'Unknown server type'),
    ('version', '', VERSION), ('version', '1', VERSION), ('version', '1.21.4-pre1', VERSION),
    ('version', 'snapshot', VERSION), ('version', '1.٢١', VERSION), ('version', 1.21, VERSION),
    ('java', 'java25', 'Unknown Java version'),
    ('heap_gb', 0, MEMORY), ('heap_gb', 33, MEMORY), ('heap_gb', 4.0, MEMORY), ('heap_gb', '4', MEMORY),
    ('heap_gb', True, MEMORY),
    ('eula', False, EULA), ('eula', 'true', EULA), ('eula', 1, EULA), ('eula', None, EULA),
])
def test_create_refuses_bad_fields(docker, data_dir, field, value, message):
    assert refused(create, **{field: value}) == (400, message)
    assert docker.created == [] and not (data_dir / 'servers').exists()


def test_create_starts_the_server_and_returns_its_dict(docker, data_dir):
    server = create('  Survival  ', version='latest')
    assert abs(server.pop('created') - time.time()) < 5
    assert server == {'id': 'survival', 'name': 'Survival', 'status': 'starting', 'type': 'PAPER',
                      'version': 'LATEST', 'java': 'java21', 'heap_gb': 4, 'port': 25565,
                      'address': 'mc.example.com'}
    assert docker.actions == [('start', 'mcpanel-survival', None)]
    assert (data_dir / 'servers' / 'survival').is_dir()


def test_address_names_the_port_unless_it_is_minecrafts_own():
    assert create('One')['address'] == 'mc.example.com'
    assert create('Two')['address'] == 'mc.example.com:25566'


@pytest.mark.parametrize('name, server_id', [
    ('My Cool Server!', 'my-cool-server'),
    ('--Ünïcode--', 'n-code'),
    ('!!!', 'server'),
    ('x' * 40, 'x' * 32),
    ('a' * 31 + ' b', 'a' * 31),  # cut to 32 would end in '-'
])
def test_id_comes_from_the_name(name, server_id):
    assert create(name)['id'] == server_id


def test_id_is_unique_among_containers_and_folders(docker, data_dir):
    assert create('Survival')['id'] == 'survival'
    assert create('Survival')['id'] == 'survival-2'
    (data_dir / 'servers' / 'survival-3').mkdir()  # an old world whose container is gone
    assert create('Survival')['id'] == 'survival-4'
    docker.add('mcpanel-creative')  # not the panel's, but it has the name
    assert create('Creative')['id'] == 'creative-2'
    assert create('x' * 40)['id'] == 'x' * 32
    assert create('x' * 40)['id'] == 'x' * 30 + '-2'


def test_free_port_takes_the_lowest_skipping_stopped_and_other_containers(docker):
    assert create('One')['port'] == 25565
    servers.stop_server('one')
    docker.add('web', ports={'80/tcp': 25566})  # another app's, never started
    docker.add('game', ports={'7777/udp': '25567-25568'})
    assert create('Two')['port'] == 25569
    servers.stop_server('two')
    assert servers.free_port() == 25570


def test_create_refuses_when_every_port_is_taken(docker, data_dir, monkeypatch):
    monkeypatch.setattr(config, 'PORT_RANGE', '25565-25566')
    docker.add('web', ports={'80/tcp': 25565, '443/tcp': 25566})
    assert refused(create) == (409, 'Every port in MCPANEL_PORT_RANGE is taken')
    assert docker.created == [] and not (data_dir / 'servers').exists()


def test_a_port_held_outside_docker_is_reported_and_nothing_is_left(docker, data_dir):
    docker.outside_ports = {25565}
    assert refused(create) == (409, 'Port 25565 is in use by something outside Docker')
    assert docker.inspect == {}
    assert docker.actions == [('remove', 'mcpanel-survival', True)]
    assert list((data_dir / 'servers').iterdir()) == []
    docker.outside_ports = set()
    assert create()['id'] == 'survival'  # nothing in the way of trying again


def test_a_port_another_container_took_while_stopped_is_reported(docker):
    create()
    servers.stop_server('survival')
    docker.add('web', ports={'80/tcp': 25565})
    docker.set_state('web', 'running')
    assert refused(servers.start_server, 'survival') == (409, 'Port 25565 is in use by another container')
    assert refused(servers.restart_server, 'survival') == (409, 'Port 25565 is in use by another container')


def test_a_failed_pull_is_reported_and_leaves_no_folder(docker, data_dir, monkeypatch):
    def pull(repository, tag=None, stream=False, decode=False):
        raise APIError('500 Server Error', explanation='pull access denied for itzg/minecraft-server\nmore')
    monkeypatch.setattr(docker.api, 'pull', pull)
    assert refused(create) == (502, 'Docker refused: pull access denied for itzg/minecraft-server')
    assert list((data_dir / 'servers').iterdir()) == []


def test_a_pull_that_fails_mid_download_says_why(docker, data_dir, monkeypatch):
    def pull(repository, tag=None, stream=False, decode=False):  # Docker said 200, then this in the stream
        return iter([{'status': 'Pulling fs layer', 'id': '4f4fb700ef54'},
                     {'error': 'failed to register layer: no space left on device\nmore'}])
    monkeypatch.setattr(docker.api, 'pull', pull)
    assert refused(create) == (502, "Couldn't download itzg/minecraft-server:java21: "
                                    'failed to register layer: no space left on device')
    assert docker.created == [] and list((data_dir / 'servers').iterdir()) == []


def test_the_image_is_pulled_only_when_missing(docker):
    create('One')
    create('Two')
    create('Three', java='java17')
    assert docker.pulled == ['itzg/minecraft-server:java21', 'itzg/minecraft-server:java17']
    docker.local_images.add('itzg/minecraft-server:java8')
    create('Four', java='java8')
    assert len(docker.pulled) == 2


def test_the_network_is_made_only_when_missing(docker):
    create('One')
    assert docker.networks_made == {'mcpanel': {'driver': 'bridge'}}
    docker.networks_made['mcpanel'] = {'made by': 'compose'}
    create('Two')
    assert docker.networks_made == {'mcpanel': {'made by': 'compose'}}


def test_list_servers_has_only_the_panels_containers_sorted_by_name(docker):
    for name in ('beta', 'Alpha', 'charlie'):
        create(name)
    docker.add('web', ports={'80/tcp': 8080})
    assert [server['name'] for server in servers.list_servers()] == ['Alpha', 'beta', 'charlie']


def test_get_server_finds_only_the_panels_containers(docker):
    create()
    assert servers.get_server('survival')['name'] == 'Survival'
    docker.add('mcpanel-impostor')  # the right name without the label
    for server_id in ('gone', 'impostor', '../survival'):
        assert refused(servers.get_server, server_id) == (404, 'No such server')


@pytest.mark.parametrize('status, exit_code, health, shown', [
    ('running', 0, None, 'running'),
    ('running', 0, 'starting', 'starting'),
    ('running', 0, 'healthy', 'running'),
    ('running', 0, 'unhealthy', 'running'),
    ('restarting', 1, None, 'restarting'),
    ('exited', 0, None, 'stopped'),
    ('exited', 143, None, 'stopped'),
    ('exited', 1, None, 'crashed'),
    ('exited', 137, None, 'crashed'),
    ('dead', 1, None, 'crashed'),
    ('created', 0, None, 'stopped'),
    ('paused', 0, None, 'stopped'),
])
def test_status_comes_from_the_container_state(docker, status, exit_code, health, shown):
    create()
    docker.set_state('mcpanel-survival', status, exit_code, health)
    assert servers.get_server('survival')['status'] == shown


def test_stop_start_and_restart_return_the_fresh_dict(docker, caplog):
    caplog.set_level(logging.INFO)
    create()
    caplog.clear()
    assert servers.stop_server('survival')['status'] == 'stopped'
    assert servers.start_server('survival')['status'] == 'starting'
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    assert servers.restart_server('survival')['status'] == 'starting'
    assert docker.actions[1:] == [('stop', 'mcpanel-survival', 60), ('start', 'mcpanel-survival', None),
                                  ('restart', 'mcpanel-survival', 60)]
    assert [(record.getMessage(), record.server) for record in caplog.records] == [
        ('Stopped server survival', 'survival'), ('Started server survival', 'survival'),
        ('Restarted server survival', 'survival')]


@pytest.mark.parametrize('action', [servers.start_server, servers.stop_server, servers.restart_server])
def test_actions_on_a_stale_or_bad_id_are_404(action):
    for server_id in ('gone', 'Bad_Id'):
        assert refused(action, server_id) == (404, 'No such server')


UNREACHABLE = (503, 'Docker is unreachable: No such file or directory')


def test_docker_unreachable_is_a_503_everywhere(docker, data_dir):
    create()
    docker.reachable = False
    assert refused(servers.list_servers) == UNREACHABLE
    assert refused(servers.get_server, 'survival') == UNREACHABLE
    assert refused(create, 'Creative') == UNREACHABLE
    for action in (servers.start_server, servers.stop_server, servers.restart_server):
        assert refused(action, 'survival') == UNREACHABLE
    assert not servers.docker_reachable()
    assert [path.name for path in (data_dir / 'servers').iterdir()] == ['survival']
    docker.reachable = True
    assert servers.docker_reachable()


def test_the_real_client_without_a_socket_is_a_503(monkeypatch, tmp_path):
    monkeypatch.setenv('DOCKER_HOST', f'unix://{tmp_path}/docker.sock')
    monkeypatch.setattr(servers, 'docker_client', REAL_DOCKER_CLIENT)
    assert refused(servers.list_servers) == UNREACHABLE
    assert not servers.docker_reachable()
