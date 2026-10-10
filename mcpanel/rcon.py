"""Commands to a running server over RCON, on the private mcpanel network at mcpanel-<id>:25575.

The Source RCON protocol: each packet is its length, an id, a type and a NUL-ended body. Log in
with the password (type 3), then send the command (type 2). A long answer comes in several packets,
so a second, empty packet of a type the server doesn't know follows it: the server answers that one
only after the whole answer to the command, which is how the end is found.
"""
import re
import socket
import struct

from flask import abort

PORT = 25575
TIMEOUT = 10  # seconds; a slow command such as save-all on a big world takes a while
MAX_COMMAND = 1446  # bytes Minecraft takes in one command packet
LOGIN, COMMAND, RESPONSE = 3, 2, 0
FORMATTING = re.compile('§.')


def address(server_id):
    """Where a server's RCON listens. Tests point this elsewhere."""
    return f'mcpanel-{server_id}', PORT


def run(server_id, password, command):
    """The server's answer to command, without its colour codes. A 502 when it can't be reached,
    which is normal while it starts."""
    try:
        with socket.create_connection(address(server_id), timeout=TIMEOUT) as conn:
            send(conn, 1, LOGIN, password)
            if read(conn)[0] == -1:
                abort(502, "The server refused the panel's RCON password; restart it")
            send(conn, 2, COMMAND, command)
            send(conn, 3, RESPONSE, '')
            answer = []
            while True:
                packet_id, body = read(conn)
                if packet_id != 2:
                    break
                answer.append(body)
    except OSError:
        abort(502, "The server's console isn't answering yet; it may still be starting")
    return FORMATTING.sub('', ''.join(answer))


def send(conn, packet_id, packet_type, body):
    data = struct.pack('<ii', packet_id, packet_type) + body.encode() + b'\0\0'
    conn.sendall(struct.pack('<i', len(data)) + data)


def read(conn):
    """(id, body) of the next packet."""
    length, = struct.unpack('<i', exactly(conn, 4))
    if not 10 <= length <= 1024 * 1024:
        raise OSError('Not an RCON packet')
    data = exactly(conn, length)
    packet_id, _ = struct.unpack('<ii', data[:8])
    return packet_id, data[8:-2].decode('utf-8', 'replace')


def exactly(conn, size):
    data = b''
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise OSError('RCON connection closed')
        data += chunk
    return data
