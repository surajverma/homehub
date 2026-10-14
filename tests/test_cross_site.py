"""Changes sent by other websites are refused, media downloads stay on supported sites, and old unsafe recipe links are hidden."""
import pytest

from app import create_app, db
from app.blueprints import media_pdfs
from app.models import Media, Note, Recipe


@pytest.fixture()
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def add_note(app, creator='Alice'):
    with app.app_context():
        note = Note(content='hello', creator=creator)
        db.session.add(note)
        db.session.commit()
        return note.id


def note_exists(app, note_id):
    with app.app_context():
        return db.session.get(Note, note_id) is not None


@pytest.mark.parametrize('fetch_site', ['cross-site', 'same-site'])
def test_change_from_another_site_is_refused(app, client, fetch_site):
    note_id = add_note(app)
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Alice'},
                       headers={'Sec-Fetch-Site': fetch_site, 'Origin': 'https://example.com'})
    assert resp.status_code == 403
    assert note_exists(app, note_id)


def test_admin_name_from_another_site_is_refused(app, client):
    resp = client.post('/notice', data={'user': 'Administrator', 'content': 'changed'},
                       headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403


def test_change_from_homehub_itself_still_works(app, client):
    note_id = add_note(app)
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Alice'},
                       headers={'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://localhost'})
    assert resp.status_code == 302
    assert not note_exists(app, note_id)


def test_older_browser_with_foreign_origin_is_refused(app, client):
    note_id = add_note(app)
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Alice'},
                       headers={'Origin': 'https://example.com'})
    assert resp.status_code == 403
    assert note_exists(app, note_id)


def test_older_browser_with_own_origin_still_works(app, client):
    note_id = add_note(app)
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Alice'},
                       headers={'Origin': 'http://localhost'})
    assert resp.status_code == 302
    assert not note_exists(app, note_id)


def test_scripts_without_browser_headers_still_work(app, client):
    note_id = add_note(app)
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Alice'})
    assert resp.status_code == 302
    assert not note_exists(app, note_id)


def test_reading_from_another_site_is_not_affected(client):
    resp = client.get('/healthz', headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 200


class FakeProcess:
    def __init__(self, lines, returncode):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def run_media_download(app, client, monkeypatch, lines, returncode):
    """Submit a link and run the download worker in place, with yt-dlp replaced by canned output."""
    commands = []

    class InlineThread:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess(lines, returncode)

    monkeypatch.setattr(media_pdfs, 'Thread', InlineThread)
    monkeypatch.setattr(media_pdfs, 'is_url_safe_for_fetch', lambda url: True)
    monkeypatch.setattr(media_pdfs, 'is_supported_site', lambda url: True)
    monkeypatch.setattr(media_pdfs.subprocess, 'Popen', fake_popen)
    client.post('/media', data={'url': 'https://example.com/video', 'creator': 'Alice'})
    assert len(commands) == 1
    with app.app_context():
        media = db.session.execute(db.select(Media)).scalar_one()
        return commands[0], media.status


def test_media_downloader_skips_generic_extractor(app, client, monkeypatch):
    cmd, _status = run_media_download(app, client, monkeypatch, [], 1)
    assert cmd[cmd.index('--use-extractors') + 1] == 'default,-generic'
    assert cmd[-1] == 'https://example.com/video'


def test_unsupported_link_gets_its_own_status_and_message(app, client, monkeypatch):
    _cmd, status = run_media_download(
        app, client, monkeypatch, ['ERROR: Unsupported URL: https://example.com/video\n'], 1)
    assert status == 'unsupported'
    page = client.get('/media').get_data(as_text=True)
    assert 'Site not supported' in page
    assert 'open it in your browser and save it' in page


def test_other_download_failures_stay_errors(app, client, monkeypatch):
    _cmd, status = run_media_download(app, client, monkeypatch, ['ERROR: HTTP Error 404\n'], 1)
    assert status == 'error'


def test_old_javascript_recipe_link_is_not_shown(app, client):
    with app.app_context():
        db.session.add(Recipe(title='Old', link='JavaScript:alert(1)', ingredients='x', instructions='y', creator='Alice'))
        db.session.add(Recipe(title='Good', link='https://example.com/soup', ingredients='x', instructions='y', creator='Alice'))
        db.session.commit()
    page = client.get('/recipes').get_data(as_text=True)
    assert 'alert(1)' not in page
    assert 'https://example.com/soup' in page


@pytest.mark.parametrize('url', ['https://example.com/clip.mp4', 'https://tinyurl.com/abc123'])
def test_unsupported_link_is_refused_before_queuing(app, client, monkeypatch, url):
    monkeypatch.setattr(media_pdfs, 'is_url_safe_for_fetch', lambda u: True)
    monkeypatch.setattr(media_pdfs, 'Thread', lambda **kwargs: pytest.fail('download was queued'))
    resp = client.post('/media', data={'url': url, 'creator': 'Alice'}, follow_redirects=True)
    assert 'This site is not supported' in resp.get_data(as_text=True)
    with app.app_context():
        assert db.session.execute(db.select(Media)).first() is None


def test_supported_site_is_recognised():
    assert media_pdfs.is_supported_site('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    assert not media_pdfs.is_supported_site('https://example.com/clip.mp4')
