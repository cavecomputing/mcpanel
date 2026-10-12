import logging

import pytest

from mcpanel import config, create_app


def test_healthz_needs_no_session(anon):
    response = anon.get('/healthz')
    assert response.status_code == 200
    assert response.json == {'ok': True, 'docker': True}


def test_healthz_says_when_docker_is_unreachable(anon, docker):
    docker.reachable = False
    response = anon.get('/healthz')
    assert response.status_code == 200
    assert response.json == {'ok': True, 'docker': False}


def test_page_sends_strangers_to_sign_in(anon):
    response = anon.get('/')
    assert response.status_code == 302
    assert response.headers['Location'].startswith('/login?next=')


def test_page_renders_for_a_platform_admin(platform_admin):
    response = platform_admin.get('/')
    assert response.status_code == 200
    html = response.text
    assert 'js/main.js' in html
    assert 'data-username="owner" data-role="platform_admin"' in html and 'Platform admin</div>' in html
    assert 'id="newServerDialog"' in html and 'href="#/users"' in html and 'id="userDialog"' in html


def test_page_offers_an_admin_only_members(admin):
    html = admin.get('/').text
    assert 'data-username="admin" data-role="admin"' in html
    assert 'id="newServerDialog"' in html and 'id="deleteServer"' in html
    assert 'href="#/users"' in html and 'id="userDialog"' in html
    assert '<option value="member">' in html and '<option value="admin">' not in html and 'value="platform_admin"' not in html


def test_page_leaves_the_admin_parts_out_for_a_member(member):
    response = member.get('/')
    assert response.status_code == 200
    html = response.text
    assert 'data-username="member" data-role="member"' in html
    assert 'newServerDialog' not in html and 'href="#/users"' not in html and 'usersView' not in html
    assert 'deleteServer' not in html


@pytest.mark.parametrize('path', ['/static/js/main.js', '/static/css/style.css', '/static/css/cavecomputing.css',
                                  '/static/favicon.svg'])
def test_static_assets_need_no_session(anon, path):
    response = anon.get(path)
    assert response.status_code == 200
    response.close()


def test_scripts_are_served_as_javascript(anon):
    """Browsers refuse a module script served as anything else."""
    response = anon.get('/static/js/api.js')
    assert response.mimetype == 'text/javascript'
    response.close()


def test_any_host_is_answered_while_no_hosts_are_allowed(anon):
    assert anon.get('/login', headers={'Host': '203.0.113.7:5000'}).status_code == 200


@pytest.mark.parametrize('host', ['panel.example.com', 'Panel.Example.com', 'panel.example.com:5000'])
def test_an_allowed_host_is_answered(anon, monkeypatch, host):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'panel.example.com')
    assert anon.get('/login', headers={'Host': host}).status_code == 200


@pytest.mark.parametrize('host', ['10.0.0.5', '10.0.0.5:5000', 'panel.example.com'])
def test_an_address_is_answered_alongside_a_name(anon, monkeypatch, host):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'panel.example.com, 10.0.0.5:5000')
    assert anon.get('/login', headers={'Host': host}).status_code == 200
    assert anon.get('/login', headers={'Host': '10.0.0.6'}).status_code == 400


@pytest.mark.parametrize('host', ['203.0.113.7', '203.0.113.7:5000', 'mc.example.com', 'evil.panel.example.com',
                                  'panel.example.com.evil.net', 'localhost'])
def test_other_hosts_are_refused(anon, monkeypatch, host):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'panel.example.com')
    assert anon.get('/login', headers={'Host': host}).status_code == 400
    assert anon.get('/static/js/main.js', headers={'Host': host}).status_code == 400
    response = anon.post('/api/servers', json={}, headers={'Host': host})
    assert response.status_code == 400 and response.json == {'error': 'Unknown host'}


def test_a_signed_in_session_is_refused_on_another_host(platform_admin, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'localhost')  # the test client's host
    assert platform_admin.get('/api/servers').status_code == 200
    cookie = f"mcpanel_session={platform_admin.get_cookie('mcpanel_session').value}"
    assert platform_admin.get('/api/servers', headers={'Host': '203.0.113.7', 'Cookie': cookie}).status_code == 400


def test_healthz_answers_on_any_host(anon, monkeypatch):
    """Docker's health check asks localhost."""
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'panel.example.com')
    assert anon.get('/healthz', headers={'Host': 'localhost:5000'}).status_code == 200


def test_the_panel_refuses_to_start_with_a_url_for_its_host(data_dir, docker, monkeypatch):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'https://panel.example.com')
    with pytest.raises(ValueError, match='MCPANEL_ALLOWED_HOSTS must be names or addresses'):
        create_app()


def test_the_panel_warns_on_start_when_its_host_is_unset(data_dir, docker, caplog):
    caplog.set_level(logging.WARNING)
    create_app()
    assert any('MCPANEL_ALLOWED_HOSTS is not set' in record.getMessage() for record in caplog.records)


def test_the_panel_does_not_warn_when_its_host_is_set(data_dir, docker, monkeypatch, caplog):
    monkeypatch.setattr(config, 'ALLOWED_HOSTS', 'panel.example.com')
    caplog.set_level(logging.WARNING)
    create_app()
    assert not any('MCPANEL_ALLOWED_HOSTS' in record.getMessage() for record in caplog.records)
