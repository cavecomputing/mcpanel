"""Request helpers shared by the routes.

They validate what the client sent and abort() with the message the frontend shows, so route
handlers can stay on the happy path.
"""
from flask import abort, request


def json_body():
    """The request's JSON object, or {} when there is no body. Anything else is a 400."""
    if not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, 'Expected a JSON object')
    return data
