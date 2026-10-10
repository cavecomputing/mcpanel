"""JSON API, mounted at /api. Each resource lives in its own child blueprint."""
import logging

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from . import account, console, files, players, servers, users

logger = logging.getLogger(__name__)

bp = Blueprint('api', __name__, url_prefix='/api')
for module in (servers, console, players, files, users, account):
    bp.register_blueprint(module.bp)


@bp.errorhandler(HTTPException)
@bp.app_errorhandler(HTTPException)  # for /api/ paths no route matches, which belong to no blueprint
def http_error(e):
    """abort(status, message) -> {"error": message} with that status. Pages keep Flask's error pages."""
    if not request.path.startswith('/api/'):
        return e
    return jsonify({'error': e.description}), e.code


@bp.errorhandler(Exception)
def unexpected_error(e):
    logger.exception('Unhandled error in %s', request.endpoint)
    return jsonify({'error': 'Something went wrong on the server'}), 500
