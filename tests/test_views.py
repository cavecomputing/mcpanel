import pytest


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


def test_page_renders_for_an_admin(admin):
    response = admin.get('/')
    assert response.status_code == 200
    html = response.text
    assert 'js/main.js' in html
    assert 'data-username="admin" data-role="admin"' in html
    assert 'id="newServerDialog"' in html and 'href="#/users"' in html


def test_page_leaves_the_admin_parts_out_for_a_member(member):
    response = member.get('/')
    assert response.status_code == 200
    html = response.text
    assert 'data-username="member" data-role="member"' in html
    assert 'newServerDialog' not in html and 'href="#/users"' not in html and 'usersView' not in html


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
