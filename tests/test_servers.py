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
                         'restart_policy', 'init', 'healthcheck', 'stop_timeout', 'mem_limit', 'log_config'}
    assert spec['image'] == 'itzg/minecraft-server:java21'
    assert spec['name'] == 'mcpanel-survival'
    assert spec['labels'] == {'mcpanel.server': 'survival', 'mcpanel.name': 'Survival'}
    assert spec['volumes'] == {str(data_dir / 'servers' / 'survival'): {'bind': '/data', 'mode': 'rw'}}
    assert spec['ports'] == {'25565/tcp': 25565}
    assert spec['network'] == 'mcpanel'
    assert spec['restart_policy'] == {'Name': 'unless-stopped'}
    assert spec['init'] is True
    assert spec['healthcheck'] == {'start_period': 600 * 10 ** 9}  # the image's own check, with longer to start
    assert spec['stop_timeout'] == 75
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
    assert set(attrs['HostConfig']) == {'Binds', 'PortBindings', 'NetworkMode', 'RestartPolicy', 'Init', 'Memory',
                                        'LogConfig'}
    assert attrs['HostConfig']['Binds'] == [f'{data_dir}/servers/survival:/data:rw']
    assert attrs['Config']['StopTimeout'] == 75  # so Docker waits that long however the server is stopped
    # Only the start period: Docker keeps the image's own test, interval and retries for the rest.
    assert {key: value for key, value in attrs['Config']['Healthcheck'].items() if value} == {'StartPeriod': 600 * 10 ** 9}


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
VERSION = 'Version must be LATEST or a Minecraft release like 26.1'
MEMORY = 'Memory must be a whole number of GB from 1 to 32'
EULA = 'Accept the Minecraft EULA to create a server'


@pytest.mark.parametrize('field, value, message', [
    ('name', '', NAME), ('name', '   ', NAME), ('name', None, NAME),
    ('name', 'x' * 41, 'Server names can be at most 40 characters'),
    ('name', 'a\nb', "Server names can't contain control characters"),
    ('type', 'paper', 'Unknown server type'), ('type', 'SPIGOT', 'Unknown server type'),
    ('version', '', VERSION), ('version', '1', VERSION), ('version', '1.21.4-pre1', VERSION),
    ('version', 'snapshot', VERSION), ('version', '1.٢١', VERSION), ('version', 1.21, VERSION),
    ('java', 'java11', 'Unknown Java version'), ('java', 'JAVA21', 'Unknown Java version'),
    ('heap_gb', 0, MEMORY), ('heap_gb', 33, MEMORY), ('heap_gb', 4.0, MEMORY), ('heap_gb', '4', MEMORY),
    ('heap_gb', True, MEMORY),
    ('eula', False, EULA), ('eula', 'true', EULA), ('eula', 1, EULA), ('eula', None, EULA),
])
def test_create_refuses_bad_fields(docker, data_dir, field, value, message):
    assert refused(create, **{field: value}) == (400, message)
    assert docker.created == [] and not (data_dir / 'servers').exists()


def test_create_leaves_the_server_stopped_and_returns_its_dict(docker, data_dir):
    server = create('  Survival  ', version='latest')
    assert abs(server.pop('created') - time.time()) < 5
    assert server == {'id': 'survival', 'name': 'Survival', 'status': 'stopped', 'type': 'PAPER',
                      'version': 'LATEST', 'java': 'java21', 'heap_gb': 4, 'port': 25565,
                      'address': 'mc.example.com'}
    # Never started, so mods and files can go in before the world is generated. Docker leaves a
    # container it never started alone when it starts up, so a reboot doesn't start it either.
    assert docker.actions == []
    assert docker.live('mcpanel-survival')['State']['Status'] == 'created'
    assert list((data_dir / 'servers' / 'survival').iterdir()) == []


@pytest.mark.parametrize('type, version, java', [
    ('PAPER', 'LATEST', 'java25'), ('PAPER', '26.1', 'java25'), ('PAPER', '27.2.1', 'java25'),
    ('PAPER', '1.21.4', 'java21'), ('PAPER', '1.20.5', 'java21'), ('PAPER', '1.20.4', 'java17'),
    ('PAPER', '1.20', 'java17'), ('PAPER', '1.17', 'java17'), ('PAPER', '1.16.5', 'java8'),
    ('PAPER', '1.8.9', 'java8'), ('FORGE', '1.16.5', 'java8'), ('FORGE', '1.17.1', 'java17'),
    ('FORGE', '1.21.1', 'java21'),
])
def test_java_left_to_the_panel_matches_the_version(docker, type, version, java):
    for left_out in ('', None):
        assert create(type=type, version=version, java=left_out)['java'] == java
    assert {spec['image'] for spec in docker.created} == {f'itzg/minecraft-server:{java}'}


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
    assert docker.pulled == [] and docker.created == [] and not (data_dir / 'servers').exists()


def test_a_port_held_outside_docker_is_reported_at_start(docker):
    docker.outside_ports = {25565}
    assert create()['status'] == 'stopped'  # Docker claims the port only when the server starts
    assert refused(servers.start_server, 'survival') == (409, 'Port 25565 is in use by something outside Docker')
    docker.outside_ports = set()
    assert servers.start_server('survival')['status'] == 'starting'  # nothing in the way of trying again


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
    assert refused(create) == (502, "Couldn't download itzg/minecraft-server:java21: "
                                    'pull access denied for itzg/minecraft-server')
    assert docker.created == [] and not (data_dir / 'servers').exists()


@pytest.mark.parametrize('pull_error', [
    APIError('500 Server Error', explanation='toomanyrequests: You have reached your pull rate limit.'),
    iter([{'error': 'Get "https://registry-1.docker.io/v2/": dial tcp: lookup registry-1.docker.io: no such host'}]),
])
def test_a_failed_pull_falls_back_to_the_copy_docker_has(docker, monkeypatch, caplog, pull_error):
    def pull(repository, tag=None, stream=False, decode=False):
        if isinstance(pull_error, Exception):
            raise pull_error
        return pull_error
    docker.local_images.add('itzg/minecraft-server:java21')
    monkeypatch.setattr(docker.api, 'pull', pull)
    assert create()['status'] == 'stopped'
    assert [record.getMessage().partition(': ')[0] for record in caplog.records if record.levelno == logging.WARNING] == [
        "Couldn't pull itzg/minecraft-server:java21, so using the copy Docker has"]


def test_a_pull_that_fails_mid_download_says_why(docker, data_dir, monkeypatch):
    def pull(repository, tag=None, stream=False, decode=False):  # Docker said 200, then this in the stream
        return iter([{'status': 'Pulling fs layer', 'id': '4f4fb700ef54'},
                     {'error': 'failed to register layer: no space left on device\nmore'}])
    monkeypatch.setattr(docker.api, 'pull', pull)
    assert refused(create) == (502, "Couldn't download itzg/minecraft-server:java21: "
                                    'failed to register layer: no space left on device')
    assert docker.created == [] and not (data_dir / 'servers').exists()


def test_the_image_is_pulled_on_every_create(docker, monkeypatch):
    # The image's tags move with its fixes and Java patch releases, so a new server gets the newest; one that
    # exists keeps the image it was made with.
    docker.local_images.add('itzg/minecraft-server:java21')
    pull = docker.api.pull
    def pull_outside_the_lock(*args, **kwargs):  # a first download takes minutes; other creates go on
        assert not servers.CREATE_LOCK.locked()
        return pull(*args, **kwargs)
    monkeypatch.setattr(docker.api, 'pull', pull_outside_the_lock)
    create('One')
    create('Two')
    create('Three', java='java17')
    assert docker.pulled == ['itzg/minecraft-server:java21'] * 2 + ['itzg/minecraft-server:java17']


def networks_of(docker, name):
    """network name -> id, of the networks a container is on."""
    return {network: settings['NetworkID'] for network, settings in docker.live(name)['NetworkSettings']['Networks'].items()}


@pytest.mark.parametrize('action', [servers.start_server, servers.restart_server])
def test_after_a_start_refused_a_port_the_next_start_rejoins_the_network(docker, action):
    create()
    servers.stop_server('survival')
    docker.add('web', ports={'80/tcp': 25565})
    docker.set_state('web', 'running')
    assert refused(servers.start_server, 'survival')[0] == 409
    assert networks_of(docker, 'mcpanel-survival') == {}  # Docker dropped it
    docker.set_state('web', 'exited')
    assert action('survival')['status'] == 'starting'
    assert networks_of(docker, 'mcpanel-survival') == {'mcpanel': docker.network_ids['mcpanel']}


@pytest.mark.parametrize('action', [servers.start_server, servers.restart_server])
def test_a_start_rejoins_a_network_made_again(docker, action):
    create()
    servers.start_server('survival')  # so it holds the old network's id
    servers.stop_server('survival')
    docker.networks.create('mcpanel')  # `docker compose down` with every server stopped, then `up`
    assert action('survival')['status'] == 'starting'
    assert networks_of(docker, 'mcpanel-survival') == {'mcpanel': docker.network_ids['mcpanel']}


def test_the_network_is_made_only_when_missing(docker):
    create('One')
    assert docker.networks_made == {'mcpanel': {'driver': 'bridge'}}
    docker.networks_made['mcpanel'] = {'made by': 'compose'}
    create('Two')
    assert docker.networks_made == {'mcpanel': {'made by': 'compose'}}


def test_another_apps_container_removed_meanwhile_fails_nothing(docker):
    docker.churn = True
    assert create()['id'] == 'survival'
    assert [server['id'] for server in servers.list_servers()] == ['survival']


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
    ('running', 0, 'unhealthy', 'unresponsive'),
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


def test_a_server_docker_keeps_restarting_says_so(docker):
    create()
    # Each restart after a crash resets the health check to starting; only the count shows the loop.
    docker.set_state('mcpanel-survival', 'running', health='starting', restarts=3)
    assert servers.get_server('survival')['status'] == 'restarting'
    assert servers.restart_server('survival')['status'] == 'starting'  # a Restart zeroes the count


def test_stop_start_and_restart_return_the_fresh_dict(docker, caplog):
    caplog.set_level(logging.INFO)
    create()
    caplog.clear()
    assert servers.start_server('survival')['status'] == 'starting'
    assert servers.stop_server('survival')['status'] == 'stopped'
    assert servers.start_server('survival')['status'] == 'starting'
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    assert servers.restart_server('survival')['status'] == 'starting'
    # The first start joins the network: Docker gives a container it never started no network id.
    assert docker.actions == [('connect', 'mcpanel-survival', 'mcpanel'), ('start', 'mcpanel-survival', None),
                              ('stop', 'mcpanel-survival', 75), ('start', 'mcpanel-survival', None),
                              ('restart', 'mcpanel-survival', 75)]
    assert [(record.getMessage(), record.server) for record in caplog.records] == [
        ('Started server survival', 'survival'), ('Stopped server survival', 'survival'),
        ('Started server survival', 'survival'), ('Restarted server survival', 'survival')]


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
