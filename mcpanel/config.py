"""Runtime configuration, read once from the environment at import time.

Other modules read these as `config.NAME` when they need them, never `from .config import NAME`,
so the tests can point them at a temporary directory.
"""
import os
from pathlib import Path

# Resolved so paths never depend on the working directory. Inside Docker this must be the same path
# as on the host: servers' data folders are bind mounts, and Docker reads those paths on the host.
DATA_DIR = Path(os.getenv('MCPANEL_DATA_DIR', 'data')).resolve()
DATABASE = DATA_DIR / 'mcpanel.db'
LOGS_DIR = DATA_DIR / 'logs'
SERVERS_DIR = DATA_DIR / 'servers'
BACKUPS_DIR = DATA_DIR / 'backups'

# The name players type to reach a server, e.g. mc.example.com. create_app() refuses to start without it.
PUBLIC_HOST = os.getenv('MCPANEL_PUBLIC_HOST', '').strip()

# The name the panel's own pages answer to, e.g. panel.example.com; requests for any other name (the
# machine's address, say) are refused. Empty answers to every name, with a warning on start.
PANEL_HOST = os.getenv('MCPANEL_PANEL_HOST', '').strip().lower()

# The host ports the panel may publish, one per server, e.g. "25565-25600". The router forwards the same range.
PORT_RANGE = os.getenv('MCPANEL_PORT_RANGE', '').strip()

# Whether signing in takes a TOTP code as well as the password: on unless set to off. MFA_ENFORCEMENT
# is the same setting under the name it was first asked for; MCPANEL_MFA_ENFORCEMENT wins.
MFA_ENFORCEMENT = (os.getenv('MCPANEL_MFA_ENFORCEMENT') or os.getenv('MFA_ENFORCEMENT') or 'on').strip().lower()


def port_range():
    """PORT_RANGE as (first, last), or a ValueError saying what is wrong with it."""
    first, sep, last = PORT_RANGE.partition('-')
    if not sep or not first.strip().isdigit() or not last.strip().isdigit():
        raise ValueError(f'MCPANEL_PORT_RANGE must look like 25565-25600, not "{PORT_RANGE}"')
    first, last = int(first), int(last)
    if not 1024 <= first <= last <= 65535:
        raise ValueError(f'MCPANEL_PORT_RANGE must be two ports from 1024 to 65535, lowest first, not "{PORT_RANGE}"')
    return first, last


def panel_host():
    """PANEL_HOST, or a ValueError when it is a URL rather than a name, which would refuse every request."""
    if '/' in PANEL_HOST:
        raise ValueError(f'MCPANEL_PANEL_HOST must be a name like panel.example.com, not "{PANEL_HOST}"')
    return PANEL_HOST


def mfa_enforced():
    """MFA_ENFORCEMENT as True or False, or a ValueError, so a typo doesn't quietly pick one."""
    if MFA_ENFORCEMENT in ('on', 'true', 'yes', '1', 'enabled'):
        return True
    if MFA_ENFORCEMENT in ('off', 'false', 'no', '0', 'disabled'):
        return False
    raise ValueError(f'MCPANEL_MFA_ENFORCEMENT must be on or off, not "{MFA_ENFORCEMENT}"')
