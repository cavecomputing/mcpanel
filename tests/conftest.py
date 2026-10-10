import os
import socket
import struct
import tempfile
import threading
import time

# app.py builds the app at import time, and create_app() needs these. Set them before anything
# imports mcpanel, and keep that first app's data out of the checkout's data/.
_IMPORT_DATA_DIR = tempfile.TemporaryDirectory(prefix='mcpanel-test-import-')
os.environ['MCPANEL_DATA_DIR'] = _IMPORT_DATA_DIR.name
os.environ['MCPANEL_PUBLIC_HOST'] = 'mc.example.com'
os.environ['MCPANEL_PORT_RANGE'] = '25565-25570'

import pyotp  # noqa: E402
import pytest  # noqa: E402

from mcpanel import accounts, auth, config, create_app, rcon, servers  # noqa: E402

from .fake_docker import FakeDocker  # noqa: E402

PASSWORD = 'correct horse battery staple'


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every path in mcpanel.config at a fresh temporary data directory."""
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'DATABASE', tmp_path / 'mcpanel.db')
    monkeypatch.setattr(config, 'LOGS_DIR', tmp_path / 'logs')
    monkeypatch.setattr(config, 'SERVERS_DIR', tmp_path / 'servers')
    monkeypatch.setattr(config, 'BACKUPS_DIR', tmp_path / 'backups')
    monkeypatch.setattr(config, 'PUBLIC_HOST', 'mc.example.com')
    monkeypatch.setattr(config, 'PORT_RANGE', '25565-25570')
    return tmp_path


@pytest.fixture
def docker(monkeypatch):
    """The fake Docker daemon every test talks to instead of a real one."""
    fake = FakeDocker()
    monkeypatch.setattr(servers, 'docker_client', lambda: fake)
    return fake


@pytest.fixture
def app(data_dir, docker):
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def clock(monkeypatch):
    """time.time() stands still at clock['now'] until a test moves it."""
    clock = {'now': time.time()}
    monkeypatch.setattr(time, 'time', lambda: clock['now'])
    return clock


@pytest.fixture
def anon(app):
    """A client that has not signed in."""
    return app.test_client()


def make_user(username, role='member', password=PASSWORD):
    """A user in the database who has had their first sign-in, as {'id', 'username', 'role', 'totp_secret'}."""
    user_id, _ = accounts.create_user(username, role, [])
    secret = pyotp.random_base32()
    accounts.finish_setup(accounts.user_by_id(user_id), password, secret, 0)
    return {'id': user_id, 'username': username, 'role': role, 'totp_secret': secret}


def signed_in_client(app, user):
    """A test client carrying a session for user, made directly rather than through the sign-in pages."""
    client = app.test_client()
    client.set_cookie('mcpanel_session', accounts.new_session(user['id'], remember=True))
    client.user = user
    return client


@pytest.fixture
def admin(app):
    """A client signed in as an admin; its user is client.user."""
    return signed_in_client(app, make_user('admin', 'admin'))


@pytest.fixture
def member(app):
    """A client signed in as a member with no servers; its user is client.user."""
    return signed_in_client(app, make_user('member', 'member'))


@pytest.fixture(autouse=True)
def no_sign_in_wait(monkeypatch):
    """A wrong password or code holds off that address's sign-ins for a second; not in tests."""
    monkeypatch.setattr(auth, 'WRONG_TRY_WAIT', 0)
    monkeypatch.setattr(auth, 'next_try', {})


def sign_in(client, username, password=PASSWORD, code=None, remember=False):
    """Sign client in through the real /login and /login/code pages; the last response.

    code defaults to the user's current TOTP code, or the next one when that was used already.
    Returns the password step's response when it didn't lead on to the code: a wrong password, or
    a one-time one, which leads on to the set-up.
    """
    data = {'username': username, 'password': password, **({'remember': 'on'} if remember else {})}
    response = client.post('/login', data=data)
    if response.headers.get('Location') != '/login/code':
        return response
    if code is None:
        with accounts.get_db() as conn:
            user = conn.execute('SELECT totp_secret, totp_last_step FROM users WHERE username = ?',
                                (username.lower(),)).fetchone()
        step = max(int(time.time()) // 30, user['totp_last_step'] + 1)
        code = pyotp.TOTP(user['totp_secret']).generate_otp(step)
    return client.post('/login/code', data={'code': code})


class FakeRcon:
    """A server's RCON on 127.0.0.1, as Minecraft answers it. answers maps a command to its answer
    (a callable gets the command); commands records what was run, passwords what logged in."""

    def __init__(self):
        self.answers, self.commands, self.passwords = {}, [], []
        self.refuse = False  # True answers a login as a wrong password
        self.listener = socket.create_server(('127.0.0.1', 0))
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            with conn:
                try:
                    while True:
                        packet_id, packet_type, body = self.read(conn)
                        if packet_type == rcon.LOGIN:
                            self.passwords.append(body)
                            rcon.send(conn, -1 if self.refuse else packet_id, rcon.COMMAND, '')
                        elif packet_type == rcon.COMMAND:
                            self.commands.append(body)
                            answer = self.answers.get(body.split(' ')[0], '')
                            answer = answer(body) if callable(answer) else answer
                            for start in range(0, max(len(answer), 1), 4096):  # as Minecraft splits it
                                rcon.send(conn, packet_id, rcon.RESPONSE, answer[start:start + 4096])
                        else:
                            rcon.send(conn, packet_id, rcon.RESPONSE, f'Unknown request {packet_type:x}')
                except (OSError, struct.error):
                    pass

    def read(self, conn):
        length, = struct.unpack('<i', rcon.exactly(conn, 4))
        data = rcon.exactly(conn, length)
        packet_id, packet_type = struct.unpack('<ii', data[:8])
        return packet_id, packet_type, data[8:-2].decode()


@pytest.fixture
def rcon_server(monkeypatch):
    """Every server's RCON, answered by one FakeRcon."""
    fake = FakeRcon()
    monkeypatch.setattr(rcon, 'address', lambda server_id: fake.listener.getsockname())
    yield fake
    fake.listener.close()
