import gzip
import json
import logging
import re
import sys

import pytest
from flask import g

from mcpanel import config, logs

log = logging.getLogger('mcpanel.test')


@pytest.fixture(autouse=True)
def restore_logging():
    """Afterwards, point the handlers away from this test's closed capture and temporary directory."""
    yield
    logs.setup_logging()


def lines(text):
    return [json.loads(line) for line in text.splitlines()]


def test_lines_are_json_on_stdout_and_the_same_in_the_log_file(capsys, data_dir):
    logs.setup_logging()
    log.info('Started %s', 'survival', extra={'server': 'survival'})
    [line] = lines(capsys.readouterr().out)
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', line.pop('ts'))
    assert line == {'level': 'INFO', 'logger': 'mcpanel.test', 'msg': 'Started survival', 'server': 'survival'}
    [in_file] = lines((data_dir / 'logs' / 'mcpanel.log').read_text())
    assert in_file == {'ts': in_file['ts'], **line}


def test_a_traceback_goes_in_exc(capsys, data_dir):
    logs.setup_logging()
    try:
        1 / 0
    except ZeroDivisionError:
        log.exception('Sum failed')
    [line] = lines(capsys.readouterr().out)
    assert line['level'] == 'ERROR'
    assert 'ZeroDivisionError' in line['exc']


def test_lines_are_plain_on_a_terminal(capsys, data_dir, monkeypatch):
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    logs.setup_logging()
    log.info('Started', extra={'server': 'survival'})
    out = capsys.readouterr().out
    assert re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO mcpanel.test: Started server=survival\n', out)
    assert lines((data_dir / 'logs' / 'mcpanel.log').read_text())[0]['msg'] == 'Started'  # the file stays JSON


def test_audit_lines_name_who_and_from_where(capsys, app, data_dir):
    logs.setup_logging()  # pytest captures each test phase anew, so log to the test's own capture
    with app.test_request_context(environ_base={'REMOTE_ADDR': '203.0.113.7'}):
        g.user = {'id': 1, 'username': 'matt', 'role': 'admin'}
        logs.audit('server_started', server='survival')
    [line] = lines((data_dir / 'logs' / 'audit.log').read_text())
    assert line['logger'] == 'mcpanel.audit'
    assert line['msg'] == line['event'] == 'server_started'
    assert (line['server'], line['user'], line['ip']) == ('survival', 'matt', '203.0.113.7')
    assert line in lines(capsys.readouterr().out)
    assert line in lines((data_dir / 'logs' / 'mcpanel.log').read_text())


def test_audit_fields_named_like_record_attributes_are_kept(data_dir):
    logs.setup_logging()
    logs.audit('server_created', name='Survival', module='x', msg='y', level='z')
    [line] = lines((data_dir / 'logs' / 'audit.log').read_text())
    assert (line['msg'], line['level']) == ('server_created', 'INFO')
    assert (line['field_name'], line['field_module'], line['field_msg'], line['field_level']) \
        == ('Survival', 'x', 'y', 'z')


def test_secret_fields_are_masked_everywhere(capsys, data_dir):
    logs.setup_logging()
    log.info('Signed in', extra={'password': 'hunter2hunter2', 'rcon_password': 'rcon-pw-1',
                                 'Authorization': 'Bearer abc123', 'form': {'totp': '864201', 'username': 'matt'},
                                 'exit_code': 1, 'status_code': 200})
    logs.audit('recovery_codes_new', recovery_codes=['aaaa-bbbb'], totp_secret='JBSWY3DPEHPK3PXP')
    logs_dir = data_dir / 'logs'
    outputs = [capsys.readouterr().out, (logs_dir / 'mcpanel.log').read_text(), (logs_dir / 'audit.log').read_text()]
    for text in outputs:
        for secret in ('hunter2hunter2', 'rcon-pw-1', 'abc123', '864201', 'aaaa-bbbb', 'JBSWY3DPEHPK3PXP'):
            assert secret not in text
    signed_in, codes = lines(outputs[0])
    assert (signed_in['password'], signed_in['rcon_password'], signed_in['Authorization']) == ('***', '***', '***')
    assert signed_in['form'] == {'totp': '***', 'username': 'matt'}
    assert (signed_in['exit_code'], signed_in['status_code']) == (1, 200)
    assert lines(outputs[2]) == [codes]
    assert (codes['recovery_codes'], codes['totp_secret']) == ('***', '***')


def test_log_files_rotate_into_gzip(data_dir, monkeypatch):
    monkeypatch.setattr(logs, 'MAX_BYTES', 300)
    logs.setup_logging()
    for n in range(10):
        log.info('Line %d', n)
    with gzip.open(data_dir / 'logs' / 'mcpanel.log.1.gz', 'rt') as rotated:
        assert lines(rotated.read())[0]['logger'] == 'mcpanel.test'
    assert not (data_dir / 'logs' / 'mcpanel.log.1').exists()


def test_setting_up_again_replaces_only_its_own_handlers(capsys, data_dir, monkeypatch):
    logs.setup_logging()
    someone_elses = logging.NullHandler()
    logging.getLogger().addHandler(someone_elses)
    monkeypatch.setattr(config, 'LOGS_DIR', data_dir / 'second')
    logs.setup_logging()
    logs.audit('sign_out')
    assert len(capsys.readouterr().out.splitlines()) == 1
    assert len((data_dir / 'second' / 'audit.log').read_text().splitlines()) == 1
    assert 'sign_out' in (data_dir / 'second' / 'mcpanel.log').read_text()
    assert (data_dir / 'logs' / 'mcpanel.log').read_text() == ''
    assert someone_elses in logging.getLogger().handlers
    logging.getLogger().removeHandler(someone_elses)


def request_lines(text):
    return [line for line in lines(text) if line['logger'] == 'mcpanel.request']


def test_requests_are_logged_except_health_checks_and_static_files(capsys, anon):
    logs.setup_logging()
    status = anon.get('/api/me?next=/somewhere').status_code
    anon.get('/healthz')
    anon.get('/static/css/style.css')
    anon.post('/api/servers', headers={'Sec-Fetch-Site': 'cross-site'})  # refused by a guard, still logged
    first, refused = request_lines(capsys.readouterr().out)
    assert first['msg'] == f'GET /api/me {status}'
    assert {k: first[k] for k in ('method', 'path', 'status', 'ip')} == \
        {'method': 'GET', 'path': '/api/me', 'status': status, 'ip': '127.0.0.1'}
    assert isinstance(first['ms'], int) and 'user' not in first
    assert (refused['method'], refused['path'], refused['status']) == ('POST', '/api/servers', 403)


def test_request_lines_name_the_signed_in_user(capsys, admin):
    logs.setup_logging()
    admin.get('/api/me')
    [line] = request_lines(capsys.readouterr().out)
    assert line['user'] == 'admin'


def test_the_dev_server_says_where_it_is_but_not_each_request(capsys, data_dir):
    logs.setup_logging()
    logs.setup_logging()  # again, as every create_app() does: still one filter
    werkzeug = logging.getLogger('werkzeug')
    werkzeug.info(' * Running on http://127.0.0.1:5000')
    werkzeug.info(' * Debugger PIN: 123-456-789')
    werkzeug.info('127.0.0.1 - - [08/Oct/2026 10:00:00] "%s" %s %s', 'GET /api/me?next=/x HTTP/1.1', '200', '-')
    assert [line['msg'] for line in lines(capsys.readouterr().out)] == [
        ' * Running on http://127.0.0.1:5000', ' * Debugger PIN: 123-456-789']
    assert werkzeug.filters.count(logs.not_request_line) == 1
