"""The game settings the Settings tab offers, kept in the server's server.properties.

The image only writes the properties it has an environment variable for, and the panel sets none of
these, so what is saved here stays. The server reads the file when it starts: a restart applies it.
The file is Java's properties format, read and written here for the keys below only; every other
line is kept as it was.

Minecraft 1.21.9 moved PvP out of the file into the pvp game rule, kept in the world: the file's pvp
line does nothing from then on, not even when a 1.21.8 world is upgraded. Only a running server reads
or changes a game rule, so on those versions PvP goes over RCON instead and takes effect at once, and
is None while the server is stopped or starting.
"""
import re

from flask import abort
from werkzeug.exceptions import BadGateway, NotFound

from . import files, rcon, servers

# key -> (default, what the value may be): a tuple of choices, (lowest, highest) for a number, a
# type for a flag or text. The defaults are the server's own, for a file that doesn't have the key yet.
SETTINGS = {
    'motd': ('A Minecraft Server', str),
    'max-players': (20, (1, 1000)),
    'difficulty': ('easy', ('peaceful', 'easy', 'normal', 'hard')),
    'gamemode': ('survival', ('survival', 'creative', 'adventure', 'spectator')),
    'hardcore': (False, bool),
    'pvp': (True, bool),
    'view-distance': (10, (3, 32)),
    'simulation-distance': (10, (3, 32)),
    'allow-flight': (False, bool),
    'white-list': (False, bool),
    'online-mode': (True, bool),
    'level-seed': ('', str),
}
MAX_TEXT = 200
PVP_RULE_SINCE = (1, 21, 9)  # the first release with the pvp game rule
# What gamerule pvp answers, with a value or without: "Game rule pvp is currently set to true" (or
# now, or already, for a value it had); 1.21.9 says "Gamerule pvp is now set to: true".
PVP_RULE = re.compile(r'pvp is (?:\w+ )?set to:? (true|false)')
KEY = re.compile(r'\s*((?:[^\s=:\\]|\\.)+)\s*[=:\s]?\s*(.*)')
ESCAPES = {'t': '\t', 'n': '\n', 'r': '\r', 'f': '\f'}


def unescape(value):
    """A value as Java writes it (\\u00a7 for §, \\n, \\:) in plain text."""
    def replace(match):
        code = match[1]
        return chr(int(code[1:], 16)) if code[0] == 'u' else ESCAPES.get(code, code)
    return re.sub(r'\\(u[0-9a-fA-F]{4}|.)', replace, value)


def escape(value):
    """Plain text as Java writes it: ASCII, with \\uXXXX for the rest, as Minecraft reads it."""
    out = []
    for char in value:
        if char in '\\=:#!':
            out.append('\\' + char)
        elif char in '\t\n\r\f':
            out.append('\\' + {'\t': 't', '\n': 'n', '\r': 'r', '\f': 'f'}[char])
        elif ' ' <= char <= '~':
            out.append(char)
        else:  # past U+FFFF, two UTF-16 halves, as Java does
            units = char.encode('utf-16-be')
            out.extend(f'\\u{int.from_bytes(units[i:i + 2]):04x}' for i in range(0, len(units), 2))
    return ''.join(out)


def as_text(value):
    return 'true' if value is True else 'false' if value is False else str(value)


def parsed(key, text):
    """A value from the file as the API shows it, or the default when it doesn't parse."""
    default, allowed = SETTINGS[key]
    if allowed is bool:
        return text.strip().lower() == 'true' if text.strip().lower() in ('true', 'false') else default
    if isinstance(allowed, tuple) and isinstance(allowed[0], int):
        return int(text) if text.strip().lstrip('-').isdigit() else default
    return text


def lines_of(server_id):
    """server.properties' lines, the RCON password masked; none when there's no file yet."""
    try:
        return files.read_text(server_id, files.PROPERTIES).splitlines()
    except NotFound:  # before the first start, which writes the file
        return []


def read(server):
    """{key: value} of every setting, the server's default for those the file leaves out, and PvP
    from the game rule on the versions that keep it there."""
    values = {key: default for key, (default, _) in SETTINGS.items()}
    for line in lines_of(server['id']):
        match = KEY.fullmatch(line)
        if match and not line.lstrip().startswith(('#', '!')):
            key = unescape(match[1])
            if key in SETTINGS:
                values[key] = parsed(key, unescape(match[2]))
    if pvp_is_game_rule(server['version']):
        values['pvp'] = pvp_rule(server)
    return values


def pvp_is_game_rule(version):
    """Whether a checked version keeps PvP in the pvp game rule: 1.21.9 on, so LATEST too."""
    return version == 'LATEST' or tuple(int(part) for part in version.split('.')) >= PVP_RULE_SINCE


def pvp_rule(server, value=None):
    """A server's pvp game rule, after setting it to value unless that is None. None when the server
    can't say: stopped, still starting, or an answer worded otherwise."""
    if server['status'] in ('stopped', 'crashed'):
        return None
    command = 'gamerule pvp' if value is None else f'gamerule pvp {as_text(value)}'
    try:
        match = PVP_RULE.search(rcon.run(server['id'], servers.rcon_password(server['id']), command))
    except BadGateway:
        return None
    return match[1] == 'true' if match else None


def checked(key, value):
    """value, or a 400 saying what a setting may be."""
    if key not in SETTINGS:
        abort(400, f'Unknown setting {key}')
    _, allowed = SETTINGS[key]
    if allowed is bool:
        if not isinstance(value, bool):
            abort(400, f'{key} is true or false')
    elif allowed is str:
        if not isinstance(value, str) or len(value) > MAX_TEXT or not value.replace('\n', '').isprintable():
            abort(400, f'{key} is text of at most {MAX_TEXT} characters')
        if key == 'level-seed' and '\n' in value:
            abort(400, 'The seed is one line')
    elif isinstance(allowed[0], int):
        if not isinstance(value, int) or isinstance(value, bool) or not allowed[0] <= value <= allowed[1]:
            abort(400, f'{key} is a whole number from {allowed[0]} to {allowed[1]}')
    elif value not in allowed:
        abort(400, f'{key} is one of {", ".join(allowed)}')
    return value


def save(server, changes):
    """Set some settings, keeping every other line of the file as it was; returns them all. PvP in
    the game rule is set first, so the file stays as it was when the server can't change it."""
    changes = {key: checked(key, value) for key, value in changes.items()}
    if 'pvp' in changes and pvp_is_game_rule(server['version']):
        pvp = changes.pop('pvp')
        if pvp_rule(server, pvp) != pvp:
            abort(409, "PvP can only change while the server is running. From Minecraft 1.21.9 it's a game "
                       'rule, kept in the world.')
    lines, done = lines_of(server['id']), set()
    for i, line in enumerate(lines):
        match = KEY.fullmatch(line)
        if match and not line.lstrip().startswith(('#', '!')) and unescape(match[1]) in changes:
            key = unescape(match[1])
            lines[i] = f'{key}={escape(as_text(changes[key]))}'
            done.add(key)
    lines += [f'{key}={escape(as_text(value))}' for key, value in changes.items() if key not in done]
    files.write(server['id'], files.PROPERTIES, [('\n'.join(lines) + '\n').encode()],
                servers.rcon_password(server['id']))
    return read(server)
