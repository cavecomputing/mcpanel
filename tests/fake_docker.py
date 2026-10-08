"""An in-memory Docker daemon: the parts of docker.DockerClient that mcpanel.servers uses.

Creating a container goes through the real SDK's translation of its options, so a misspelt option
fails as it would for real and attrs read the way docker inspect reports them. A container object is
a snapshot of them until reload(), as in the SDK. Failures are the real docker.errors exceptions.
"""
import copy
import uuid
from datetime import datetime, timezone

from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import _create_container_args
from docker.types import ContainerConfig

API_VERSION = '1.47'


class FakeDocker:
    """Knobs: reachable = False fails every call as when the socket is gone; outside_ports are host
    ports a program outside Docker holds. created, pulled and actions record what was done."""

    def __init__(self):
        self.reachable = True
        self.outside_ports = set()
        self.created = []         # the options of each containers.create()
        self.pulled = []          # 'repository:tag' of each images.pull()
        self.actions = []         # (action, container name, argument) of each start, stop, restart, remove
        self.local_images = set()
        self.networks_made = {}   # network name -> the options it was created with
        self.inspect = {}         # container id -> attrs, as docker inspect reports them
        self.containers, self.images, self.networks = Containers(self), Images(self), Networks(self)

    def answer(self):
        if not self.reachable:
            raise DockerException("Error while fetching server API version: ('Connection aborted.', "
                                  "FileNotFoundError(2, 'No such file or directory'))") \
                from FileNotFoundError(2, 'No such file or directory')

    def ping(self):
        self.answer()
        return True

    def add(self, name, image='nginx:latest', **options):
        """A container made from containers.create() options, skipping the image and network checks.
        add('web', ports={'80/tcp': 25566}) is another app's container holding port 25566."""
        if any(attrs['Name'] == f'/{name}' for attrs in self.inspect.values()):
            raise APIError('409 Client Error: Conflict',
                           explanation=f'Conflict. The container name "/{name}" is already in use')
        config = ContainerConfig(API_VERSION, **_create_container_args(
            {**options, 'image': image, 'command': None, 'version': API_VERSION}))
        container_id = uuid.uuid4().hex + uuid.uuid4().hex
        self.inspect[container_id] = {
            'Id': container_id,
            'Name': f'/{name}',
            'Created': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f123Z'),  # nanoseconds
            'Config': {key: value for key, value in config.items() if key not in ('HostConfig', 'NetworkingConfig')},
            'HostConfig': config['HostConfig'],
            'State': {'Status': 'created', 'Running': False, 'ExitCode': 0},
        }
        return Container(self, container_id)

    def set_state(self, name, status, exit_code=0, health=None):
        """Put a container in a state: set_state('mcpanel-x', 'exited', exit_code=1), or 'running'
        with health='starting'."""
        state = {'Status': status, 'Running': status in ('running', 'restarting'), 'ExitCode': exit_code}
        if health:
            state['Health'] = {'Status': health}
        self.live(name)['State'] = state

    def live(self, key):
        """The daemon's own attrs of a container, by id or name."""
        self.answer()
        for attrs in self.inspect.values():
            if key in (attrs['Id'], attrs['Name'][1:]):
                return attrs
        raise NotFound(f'No such container: {key}')

    def start_container(self, attrs):
        """Start a container, refusing a host port that something else holds, in Docker's words."""
        endpoint = f'driver failed programming external connectivity on endpoint {attrs["Name"][1:]}'
        for port in host_ports(attrs):
            if port in self.outside_ports:
                raise APIError('500 Server Error', explanation=f'{endpoint}: Error starting userland proxy: '
                                                               f'listen tcp4 0.0.0.0:{port}: bind: address already in use')
            if any(port in host_ports(other) for other in self.inspect.values()
                   if other is not attrs and other['State']['Running']):
                raise APIError('500 Server Error',
                               explanation=f'{endpoint}: Bind for 0.0.0.0:{port} failed: port is already allocated')
        # The image's health check says starting until the server answers.
        attrs['State'] = {'Status': 'running', 'Running': True, 'ExitCode': 0, 'Health': {'Status': 'starting'}}


def host_ports(attrs):
    ports = set()
    for bindings in attrs['HostConfig'].get('PortBindings', {}).values():
        for binding in bindings:
            first, _, last = binding['HostPort'].partition('-')  # a host range is "a-b"
            ports.update(range(int(first), int(last or first) + 1))
    return ports


class Containers:
    def __init__(self, fake):
        self.fake = fake

    def create(self, image, command=None, **options):
        self.fake.answer()
        self.fake.created.append({'image': image, **options})
        if image not in self.fake.local_images:
            raise ImageNotFound(f'No such image: {image}')
        if options.get('network') not in (None, *self.fake.networks_made):
            raise NotFound(f'network {options["network"]} not found')
        return self.fake.add(image=image, **options)

    def list(self, all=False, filters=None, ignore_removed=False):
        self.fake.answer()
        label = (filters or {}).get('label')
        return [Container(self.fake, container_id) for container_id, attrs in self.fake.inspect.items()
                if (all or attrs['State']['Running']) and (label is None or label in (attrs['Config']['Labels'] or {}))]

    def get(self, key):
        return Container(self.fake, self.fake.live(key)['Id'])


class Container:
    """A snapshot of one container's attrs until reload(), like docker.models.containers.Container."""

    def __init__(self, fake, container_id):
        self.fake, self.id = fake, container_id
        self.attrs = copy.deepcopy(fake.inspect[container_id])

    name = property(lambda self: self.attrs['Name'][1:])
    labels = property(lambda self: self.attrs['Config']['Labels'] or {})
    status = property(lambda self: self.attrs['State']['Status'])

    def reload(self):
        self.attrs = copy.deepcopy(self.fake.live(self.id))

    def start(self):
        self.fake.start_container(self.fake.live(self.id))
        self.fake.actions.append(('start', self.name, None))

    def stop(self, timeout=None):
        self.fake.live(self.id)['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}
        self.fake.actions.append(('stop', self.name, timeout))

    def restart(self, timeout=None):
        attrs = self.fake.live(self.id)
        attrs['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}
        self.fake.start_container(attrs)
        self.fake.actions.append(('restart', self.name, timeout))

    def remove(self, force=False):
        if self.fake.live(self.id)['State']['Running'] and not force:
            raise APIError('409 Client Error: Conflict', explanation='You cannot remove a running container')
        del self.fake.inspect[self.id]
        self.fake.actions.append(('remove', self.name, force))


class Images:
    def __init__(self, fake):
        self.fake = fake

    def get(self, name):
        self.fake.answer()
        if name not in self.fake.local_images:
            raise ImageNotFound(f'No such image: {name}')

    def pull(self, repository, tag=None):
        self.fake.answer()
        self.fake.pulled.append(f'{repository}:{tag}')
        self.fake.local_images.add(f'{repository}:{tag}')


class Networks:
    def __init__(self, fake):
        self.fake = fake

    def get(self, name):
        self.fake.answer()
        if name not in self.fake.networks_made:
            raise NotFound(f'network {name} not found')

    def create(self, name, **options):
        self.fake.answer()
        self.fake.networks_made[name] = options
