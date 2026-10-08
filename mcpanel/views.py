"""The page, and /healthz."""
from flask import Blueprint, render_template

from . import servers

bp = Blueprint('views', __name__)


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/healthz')
def healthz():
    """Open to anyone (auth.OPEN_ENDPOINTS names it), for Docker's health check and uptime monitors."""
    return {'ok': True, 'docker': servers.docker_reachable()}
