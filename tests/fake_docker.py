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
    ports a program outside Docker holds; churn = True has another app remove a container between
    each listing and its inspect, as a `docker run --rm` job would. created, pulled and actions
    record what was done."""

    def __init__(self):
        self.reachable = True
        self.outside_ports = set()
        self.churn = False
        self.created = []         # the arguments of each api.create_container()
        self.pulled = []          # 'repository:tag' of each api.pull()
        self.actions = []         # (action, container name, argument) of each connect, start, stop, restart, rename, remove
        self.local_images = set()
        self.networks_made = {}   # network name -> the options it was created with
        self.network_ids = {}     # network name -> its id, new each time it is made
        self.inspect = {}         # container id -> attrs, as docker inspect reports them
        self.output = {}          # container name -> [(time as Docker writes it, line)] of its log
        self.containers, self.images, self.networks = Containers(self), Images(self), Networks(self)
        self.api = Api(self)

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
        return Container(self, self.make(name, _create_container_args({**options, 'image': image, 'version': API_VERSION})))

    def make(self, name, options):
        """A new container's id, from api.create_container() arguments."""
        if any(attrs['Name'] == f'/{name}' for attrs in self.inspect.values()):
            raise APIError('409 Client Error: Conflict',
                           explanation=f'Conflict. The container name "/{name}" is already in use')
        config = ContainerConfig(API_VERSION, **{'command': None, **options})
        network = config['HostConfig'].get('NetworkMode')
        container_id = uuid.uuid4().hex + uuid.uuid4().hex
        self.inspect[container_id] = {
            'Id': container_id,
            'Name': f'/{name}',
            'Created': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f123Z'),  # nanoseconds
            'Config': {key: value for key, value in config.items() if key not in ('HostConfig', 'NetworkingConfig')},
            'HostConfig': config['HostConfig'],
            # Docker fills in the network's id only when a container first starts.
            'NetworkSettings': {'Networks': {network: {'NetworkID': ''}} if network else {}},
            'State': {'Status': 'created', 'Running': False, 'ExitCode': 0},
            'RestartCount': 0,
        }
        return container_id

    def set_state(self, name, status, exit_code=0, health=None, restarts=0):
        """Put a container in a state: set_state('mcpanel-x', 'exited', exit_code=1), or 'running'
        with health='starting', and restarts=3 when Docker has restarted it 3 times since its start."""
        state = {'Status': status, 'Running': status in ('running', 'restarting'), 'ExitCode': exit_code}
        if health:
            state['Health'] = {'Status': health}
        self.live(name)['State'] = state
        self.live(name)['RestartCount'] = restarts

    def live(self, key):
        """The daemon's own attrs of a container, by id or name."""
        self.answer()
        for attrs in self.inspect.values():
            if key in (attrs['Id'], attrs['Name'][1:]):
                return attrs
        raise NotFound(f'No such container: {key}')

    def start_container(self, attrs):
        """Start a container, refusing a host port that something else holds, or a network that is
        gone, in Docker's words. Refused a port, it loses its network, as with Docker 29."""
        for network in attrs['NetworkSettings']['Networks'].values():
            if network['NetworkID'] and network['NetworkID'] not in self.network_ids.values():
                raise APIError('500 Server Error', explanation='failed to set up container networking: '
                                                               f'network {network["NetworkID"]} not found')
        endpoint = f'driver failed programming external connectivity on endpoint {attrs["Name"][1:]}'
        for port in host_ports(attrs):
            if port in self.outside_ports:
                attrs['NetworkSettings']['Networks'] = {}
                raise APIError('500 Server Error', explanation=f'{endpoint}: Error starting userland proxy: '
                                                               f'listen tcp4 0.0.0.0:{port}: bind: address already in use')
            if any(port in host_ports(other) for other in self.inspect.values()
                   if other is not attrs and other['State']['Running']):
                attrs['NetworkSettings']['Networks'] = {}
                raise APIError('500 Server Error',
                               explanation=f'{endpoint}: Bind for 0.0.0.0:{port} failed: port is already allocated')
        # The image's health check says starting until the server answers. A start or restart zeroes the count.
        attrs['State'] = {'Status': 'running', 'Running': True, 'ExitCode': 0, 'Health': {'Status': 'starting'}}
        attrs['RestartCount'] = 0


def host_ports(attrs):
    ports = set()
    for bindings in attrs['HostConfig'].get('PortBindings', {}).values():
        for binding in bindings:
            first, _, last = binding['HostPort'].partition('-')  # a host range is "a-b"
            ports.update(range(int(first), int(last or first) + 1))
    return ports


def log_seconds(stamp):
    whole, _, fraction = stamp.removesuffix('Z').partition('.')
    return datetime.fromisoformat(whole + '+00:00').timestamp() + float(f'0.{fraction or 0}')


class Api:
    """The low-level APIClient calls that servers.py makes."""
    api_version = API_VERSION

    def __init__(self, fake):
        self.fake = fake

    def create_container(self, image, name=None, **options):
        """For stop_timeout, which containers.create() doesn't take."""
        self.fake.answer()
        self.fake.created.append({'image': image, 'name': name, **options})
        if image not in self.fake.local_images:
            raise ImageNotFound(f'No such image: {image}')
        network = options['host_config'].get('NetworkMode')
        if network not in (None, *self.fake.networks_made):
            raise NotFound(f'network {network} not found')
        return {'Id': self.fake.make(name, {'image': image, **options})}

    def pull(self, repository, tag=None, stream=False, decode=False):
        """The progress stream, which carries a download's errors; images.pull() drops them."""
        self.fake.answer()
        self.fake.pulled.append(f'{repository}:{tag}')
        self.fake.local_images.add(f'{repository}:{tag}')
        return iter([{'status': f'Pulling from {repository}', 'id': tag}, {'status': 'Download complete'}])


class Containers:
    def __init__(self, fake):
        self.fake = fake

    def list(self, all=False, filters=None, ignore_removed=False):
        self.fake.answer()
        if self.fake.churn and not ignore_removed:
            raise NotFound('No such container: 588b9f20c2a1')
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

    def logs(self, timestamps=False, since=None, tail='all'):
        """The log as docker logs --timestamps writes it. since is inclusive, as Docker's is."""
        lines = [(stamp, text) for stamp, text in self.fake.output.get(self.name, [])
                 if since is None or log_seconds(stamp) >= since]
        if tail != 'all':
            lines = lines[-tail:]
        return ''.join(f'{stamp} {text}\n' if timestamps else f'{text}\n' for stamp, text in lines).encode()

    def rename(self, name):
        if any(attrs['Name'] == f'/{name}' for attrs in self.fake.inspect.values()):
            raise APIError('409 Client Error: Conflict', explanation=f'Conflict. The container name "/{name}" is already in use')
        self.fake.live(self.id)['Name'] = f'/{name}'
        self.fake.actions.append(('rename', self.name, name))
        self.attrs['Name'] = f'/{name}'

    def remove(self):
        if self.fake.live(self.id)['State']['Running']:
            raise APIError('409 Client Error: Conflict', explanation='cannot remove container: container is running')
        del self.fake.inspect[self.id]
        self.fake.actions.append(('remove', self.name, None))

    def restart(self, timeout=None):
        attrs = self.fake.live(self.id)
        attrs['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0}
        self.fake.start_container(attrs)
        self.fake.actions.append(('restart', self.name, timeout))


class Images:
    def __init__(self, fake):
        self.fake = fake

    def get(self, name):
        self.fake.answer()
        if name not in self.fake.local_images:
            raise ImageNotFound(f'No such image: {name}')


class Networks:
    def __init__(self, fake):
        self.fake = fake

    def get(self, name):
        self.fake.answer()
        if name not in self.fake.networks_made:
            raise NotFound(f'network {name} not found')
        return Network(self.fake, name)

    def create(self, name, **options):
        """Make a network; making one again, as `docker compose down` and `up` do, gives it a new id."""
        self.fake.answer()
        self.fake.networks_made[name] = options
        self.fake.network_ids[name] = uuid.uuid4().hex + uuid.uuid4().hex
        return Network(self.fake, name)


class Network:
    def __init__(self, fake, name):
        self.fake, self.name, self.id = fake, name, fake.network_ids[name]

    def connect(self, container):
        networks = self.fake.live(container.id)['NetworkSettings']['Networks']
        if networks.get(self.name, {}).get('NetworkID') == self.id:
            raise APIError('403 Client Error: Forbidden',
                           explanation=f'endpoint with name {container.name} already exists in network {self.name}')
        networks[self.name] = {'NetworkID': self.id}
        self.fake.actions.append(('connect', container.name, self.name))
