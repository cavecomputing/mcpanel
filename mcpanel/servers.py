"""Everything that talks to Docker. A server is an itzg/minecraft-server container labelled
mcpanel.server=<id>; Docker is the only record of it, so every answer here is read from Docker.

The panel holds the Docker socket, which is root on the host: container_spec() is the one place a
container's settings are decided, from checked fields only.
"""
import functools
import itertools
import logging
import os
import re
import secrets
import threading
from contextlib import contextmanager, suppress
from datetime import datetime

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import _create_container_args
from flask import abort

from . import config

logger = logging.getLogger(__name__)

IMAGE = 'itzg/minecraft-server'
# The image's Java tags we offer, newest first. java25 runs current Minecraft; older releases need older
# Java, which java_tag() picks when the form leaves it to the panel.
JAVA_TAGS = ('java25', 'java21', 'java17', 'java8')
# The Java each Minecraft release needs, newest first (the Minecraft wiki's table, which the image's
# docs point to): 26.1 on needs 25, 1.20.5 on 21, 1.17 on 17 (16 at least), anything older 8.
JAVA_FOR = (((26,), 'java25'), ((1, 20, 5), 'java21'), ((1, 17), 'java17'), ((), 'java8'))
TYPES = ('VANILLA', 'PAPER', 'PURPUR', 'FABRIC', 'FORGE', 'NEOFORGE', 'QUILT')
MAX_HEAP_GB = 32
MAX_NAME = 40  # characters in a display name
NETWORK = 'mcpanel'  # where the panel reaches each server's RCON by container name
# How long a server gets to stop before Docker kills it: the image's own STOP_DURATION (60 s) for the
# world to save, plus room for the image to exit after it.
STOP_SECONDS = 75
# How long the health check gives a start before it counts: a first start downloads the server, and
# a modded one installs its loader, which can take minutes.
FIRST_START_SECONDS = 10 * 60
GIB = 1024 ** 3

ID = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?')
VERSION = re.compile(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?')

# Held from picking a new server's id and port until its container exists, so two creates never
# claim the same one. In memory, which is one reason the panel runs one worker process.
CREATE_LOCK = threading.Lock()


def check_id(server_id):
    """server_id if it is a well-formed id, else the 404 an unknown server gets. Checked before an id
    reaches a container name, a label or a path."""
    if not isinstance(server_id, str) or not ID.fullmatch(server_id):
        abort(404, 'No such server')
    return server_id


@functools.cache  # a failed connection raises, so it isn't cached and the next call tries again
def docker_client():
    """The Docker daemon the environment names, normally /var/run/docker.sock. Tests replace this."""
    return docker.from_env()


def docker_reachable():
    """Whether the Docker daemon answers."""
    try:
        return docker_client().ping()
    except (DockerException, OSError):
        return False


@contextmanager
def docker_errors(game_port=None):
    """Turn what the Docker SDK raises into abort() with the message the UI shows.

    APIError is the daemon saying no. Any other DockerException, or the OSError of a connection that
    drops mid-call, means it can't be reached. Keep file work outside: its OSErrors aren't Docker's.
    """
    try:
        yield
    except APIError as e:
        explanation = str(e.explanation or e)
        if game_port and 'address already in use' in explanation:
            abort(409, f'Port {game_port} is in use by something outside Docker')
        if game_port and 'port is already allocated' in explanation:
            abort(409, f'Port {game_port} is in use by another container')
        abort(502, f'Docker refused: {first_line(explanation)}')
    except (DockerException, OSError) as e:
        abort(503, f'Docker is unreachable: {root_cause(e)}')


def root_cause(error):
    """The innermost of chained errors in its own words, e.g. the OS's "Permission denied" for the
    socket rather than the layers of HTTP client wrapped around it."""
    while error.__cause__ or error.__context__:
        error = error.__cause__ or error.__context__
    return first_line(getattr(error, 'strerror', None) or str(error))


def first_line(text):
    return text.strip().partition('\n')[0][:200]


def list_servers():
    """Every server's dict, sorted by name."""
    with docker_errors():
        containers = docker_client().containers.list(all=True, filters={'label': 'mcpanel.server'},
                                                     ignore_removed=True)
        return sorted((server_dict(container) for container in containers),
                      key=lambda server: (server['name'].casefold(), server['id']))


def get_server(server_id):
    check_id(server_id)
    with docker_errors():
        return server_dict(server_container(docker_client(), server_id))


def server_container(client, server_id):
    """The container of a checked id, or a 404: an id Docker no longer has is stale, not an error."""
    try:
        container = client.containers.get(f'mcpanel-{server_id}')
    except NotFound:
        abort(404, 'No such server')
    if container.labels.get('mcpanel.server') != server_id:
        abort(404, 'No such server')
    return container


def server_dict(container):
    """What the UI shows of a server. Never the container's env, which holds the RCON password."""
    attrs = container.attrs
    env = dict(item.partition('=')[::2] for item in attrs['Config'].get('Env') or [])
    heap = env.get('MEMORY', '').removesuffix('G')
    game_port = next(host_ports(container), None)
    return {
        'id': container.labels['mcpanel.server'],
        'name': container.labels.get('mcpanel.name', ''),
        'status': status_of(attrs),
        'type': env.get('TYPE', ''),
        'version': env.get('VERSION', ''),
        'java': attrs['Config']['Image'].partition(':')[2],
        'heap_gb': int(heap) if heap.isdigit() else None,
        'port': game_port,
        'address': config.PUBLIC_HOST if game_port == 25565 else f'{config.PUBLIC_HOST}:{game_port}',
        # Docker writes UTC with nanoseconds, more digits than Python parses.
        'created': int(datetime.fromisoformat(attrs['Created'][:19] + '+00:00').timestamp()),
    }


def status_of(attrs):
    """running, starting, restarting, unresponsive, crashed or stopped, from a container's attrs."""
    state = attrs['State']
    if state['Status'] == 'running':
        # The image's health check says starting until the server answers players. Docker restarts a
        # crashing server at once and resets its health each time, so a server that will never come
        # up would say starting for ever: RestartCount, which Start and Restart zero, tells them apart.
        health = (state.get('Health') or {}).get('Status')
        if health == 'starting':
            return 'restarting' if attrs.get('RestartCount') else 'starting'
        # Up, but not answering players: hung, or still loading past FIRST_START_SECONDS. Docker
        # doesn't restart an unhealthy container, so say so rather than "running".
        return 'unresponsive' if health == 'unhealthy' else 'running'
    if state['Status'] == 'restarting':
        return 'restarting'
    # 143 is the server ending on Docker's SIGTERM, i.e. stopped on purpose.
    if state['Status'] in ('exited', 'dead') and state.get('ExitCode') not in (0, 143):
        return 'crashed'
    return 'stopped'


def host_ports(container):
    """The host ports a container publishes. Read from its settings rather than its network, which a
    stopped container doesn't have, so a stopped container still holds its ports."""
    for bindings in (container.attrs['HostConfig'].get('PortBindings') or {}).values():
        for binding in bindings or []:
            first, _, last = (binding.get('HostPort') or '').partition('-')  # a host range is "a-b"
            if first.isdigit():
                yield from range(int(first), int(last or first) + 1)


def free_port():
    """The lowest port in MCPANEL_PORT_RANGE that no container publishes, running or stopped, the
    panel's or not. Call under CREATE_LOCK to claim it. A port held outside Docker only shows when a
    server starts."""
    first, last = config.port_range()
    taken = {port for container in docker_client().containers.list(all=True, ignore_removed=True)
             for port in host_ports(container)}
    port = next((port for port in range(first, last + 1) if port not in taken), None)
    if port is None:
        abort(409, 'Every port in MCPANEL_PORT_RANGE is taken')
    return port


def free_id(name, container_names):
    """A new server's id: its name's letters and digits joined by '-', then -2, -3... past any id a
    container or a folder already has, so an old world is never picked up by accident."""
    base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'server'
    for n in itertools.count(1):
        suffix = f'-{n}' if n > 1 else ''
        server_id = base[:32 - len(suffix)].strip('-') + suffix
        if f'mcpanel-{server_id}' not in container_names and not os.path.lexists(config.SERVERS_DIR / server_id):
            return server_id


def container_spec(server_id, name, type, version, java, heap_gb, game_port):
    """The keyword arguments for containers.create(), plus stop_timeout, from checked fields only.
    Nothing privileged and no mount but the server's own folder: whoever chooses these settings owns
    the host."""
    heap = heap_gb * GIB
    return {
        'image': f'{IMAGE}:{java}',
        'name': f'mcpanel-{server_id}',
        'labels': {'mcpanel.server': server_id, 'mcpanel.name': name},
        'environment': {
            'EULA': 'TRUE',  # create_server() refuses unless the EULA box was ticked
            'TYPE': type,
            'VERSION': version,
            'MEMORY': f'{heap_gb}G',
            # The panel's own user, so the panel can read and edit the server's files.
            'UID': str(os.getuid()),
            'GID': str(os.getgid()),
            'RCON_PASSWORD': secrets.token_urlsafe(24),
        },
        'ports': {'25565/tcp': game_port},
        'volumes': {str(config.SERVERS_DIR / server_id): {'bind': '/data', 'mode': 'rw'}},
        'network': NETWORK,
        'restart_policy': {'Name': 'unless-stopped'},
        # Docker's tiny init as PID 1 passes the stop signal on, so stopping a server while the image's
        # setup scripts still run (a download, a modded install) ends them at once instead of waiting
        # out the stop timeout; bash as PID 1 ignores it.
        'init': True,
        # The image's own health check, with time for a first start before it counts (in nanoseconds).
        'healthcheck': {'start_period': FIRST_START_SECONDS * 10 ** 9},
        # However it is stopped (the panel, a reboot, a `docker stop`), the server gets this long to
        # save the world before Docker kills it, not Docker's default 10 seconds.
        'stop_timeout': STOP_SECONDS,
        # The heap plus room for the JVM's own memory. Past this the kernel kills the server.
        'mem_limit': heap + max(GIB, heap // 4),
        # Docker's default json-file log never rotates.
        'log_config': {'type': 'json-file', 'config': {'max-size': '10m', 'max-file': '3'}},
    }


def create_server(name, type, version, java, heap_gb, eula):
    """Create a server from the new-server form's fields, stopped, and return its dict. java may be
    empty, for the tag the version needs. Nothing starts it but Start: mods and files go in first,
    before the world is generated."""
    name, version = checked_name(name), checked_version(version)
    if type not in TYPES:
        abort(400, 'Unknown server type')
    if java in (None, ''):
        java = java_tag(version)
    elif java not in JAVA_TAGS:
        abort(400, 'Unknown Java version')
    if not isinstance(heap_gb, int) or isinstance(heap_gb, bool) or not 1 <= heap_gb <= MAX_HEAP_GB:
        abort(400, f'Memory must be a whole number of GB from 1 to {MAX_HEAP_GB}')
    if eula is not True:
        abort(400, 'Accept the Minecraft EULA to create a server')

    with docker_errors():
        free_port()  # a full range refuses at once, not after a download; the lock below decides the port
        ensure_image(docker_client(), java)  # before the lock: a first download takes minutes
    with CREATE_LOCK:
        with docker_errors():
            client = docker_client()
            server_id = free_id(name, {container.name for container in
                                       client.containers.list(all=True, ignore_removed=True)})
            game_port = free_port()
        folder = config.SERVERS_DIR / server_id
        folder.mkdir(parents=True)  # never exist_ok: free_id() saw no folder, so one now is someone else's
        try:
            with docker_errors():
                ensure_network(client)
                spec = container_spec(server_id, name, type, version, java, heap_gb, game_port)
                # containers.create() doesn't take stop_timeout, so do what it does with the SDK's
                # private _create_container_args (uv.lock pins the SDK; the fake uses it too, so the
                # suite catches a change) and hand the API call stop_timeout as well.
                stop_timeout = spec.pop('stop_timeout')
                client.api.create_container(**_create_container_args({**spec, 'version': client.api.api_version}),
                                            stop_timeout=stop_timeout)
        except Exception:
            # Leave nothing behind: no container was made, and nothing has run in the folder.
            with suppress(OSError):
                folder.rmdir()
            raise
    logger.info('Created server %s', server_id, extra={'server': server_id, 'server_name': name,
                                                       'port': game_port})
    return get_server(server_id)


def checked_name(name):
    """A display name stripped of surrounding spaces, or a 400 saying what is wrong with it."""
    if not isinstance(name, str) or not name.strip():
        abort(400, 'Give the server a name')
    name = name.strip()
    if len(name) > MAX_NAME:
        abort(400, f'Server names can be at most {MAX_NAME} characters')
    if not name.isprintable():
        abort(400, "Server names can't contain control characters")
    return name


def checked_version(version):
    """LATEST in any case, or a release number like 26.1 or 1.21.4."""
    version = version.strip() if isinstance(version, str) else ''
    if version.upper() == 'LATEST':
        return 'LATEST'
    if not VERSION.fullmatch(version):
        abort(400, 'Version must be LATEST or a Minecraft release like 26.1')
    return version


def java_tag(version):
    """The Java tag that runs a checked Minecraft version, the newest for LATEST. Forge before 1.17
    gets java8 with the rest, as the image's docs say it must; 1.17 itself needs Java 16."""
    if version == 'LATEST':
        return JAVA_TAGS[0]
    number = tuple(int(part) for part in version.split('.'))
    return next(tag for first, tag in JAVA_FOR if number >= first)


def ensure_image(client, java):
    """Pull the image's tag, which its maintainers move along with the image's fixes and Java patch
    releases. When the pull fails (Docker Hub's rate limit, no internet) a copy Docker already has will do."""
    logger.info('Pulling %s:%s', IMAGE, java)
    try:
        # images.pull() drops an error in the progress stream (a download cut off, a full disk) and
        # then says "No such image", so read the stream for it.
        error = next((event['error'] for event in client.api.pull(IMAGE, tag=java, stream=True, decode=True)
                      if 'error' in event), None)
    except APIError as e:
        error = str(e.explanation or e)
    if error is None:
        return
    try:
        client.images.get(f'{IMAGE}:{java}')
    except ImageNotFound:
        abort(502, f"Couldn't download {IMAGE}:{java}: {first_line(error)}")
    logger.warning("Couldn't pull %s:%s, so using the copy Docker has: %s", IMAGE, java, first_line(error))


def ensure_network(client):
    """The mcpanel network, made if missing. compose.yml makes it; this covers a panel run outside compose."""
    try:
        return client.networks.get(NETWORK)
    except NotFound:
        return client.networks.create(NETWORK, driver='bridge')


def on_network(container):
    """container, put back on the mcpanel network first if it isn't on it. A start Docker refuses (a
    port clash, from the panel or at boot) drops the container's network, and `docker compose down`
    with every server stopped deletes the network under it. Started either way, a server would run
    with no network and no published port, or not at all."""
    network = ensure_network(docker_client())
    if container.attrs['NetworkSettings']['Networks'].get(NETWORK, {}).get('NetworkID') != network.id:
        network.connect(container)  # Docker refuses this when it is on the network already
    return container


def start_server(server_id):
    return change(server_id, 'Started', lambda container: on_network(container).start())


def stop_server(server_id):
    return change(server_id, 'Stopped', lambda container: container.stop(timeout=STOP_SECONDS))


def restart_server(server_id):
    return change(server_id, 'Restarted', lambda container: on_network(container).restart(timeout=STOP_SECONDS))


def change(server_id, done, action):
    """Run action on a server's container, then return its fresh dict."""
    check_id(server_id)
    with docker_errors():
        container = server_container(docker_client(), server_id)
    with docker_errors(next(host_ports(container), None)):
        action(container)
        container.reload()
    logger.info('%s server %s', done, server_id, extra={'server': server_id})
    return server_dict(container)
