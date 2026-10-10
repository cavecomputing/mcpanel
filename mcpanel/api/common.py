"""Request helpers shared by the routes.

They validate what the client sent and abort() with the message the frontend shows, so route
handlers can stay on the happy path.
"""
from flask import abort, g, request

from .. import accounts, servers


def json_body():
    """The request's JSON object, or {} when there is no body. Anything else is a 400."""
    if not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, 'Expected a JSON object')
    return data


def usable_server(server_id):
    """The dict of a server the signed-in user may use. One they may not gets the same 404 as a
    missing one, so ids can't be probed."""
    if not accounts.may_use(g.user, servers.check_id(server_id)):
        abort(404, 'No such server')
    return servers.get_server(server_id)
