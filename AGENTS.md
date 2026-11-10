# Working on HomeHub

This file is for anyone changing HomeHub, people and coding agents alike. Read it before you open a pull request.

HomeHub is a self-hosted family app: Flask, SQLAlchemy on SQLite, Jinja templates and Tailwind 3, shipped as a Docker image. It runs on a home network for one household, and people upgrade by pulling a new image over their existing data. Most rules below exist to keep those upgrades safe.

## Layout

- `app/` holds the application: `blueprints/` for the pages, `api/` for the token-protected JSON API under `/api/v1` (including the MCP endpoint), `models.py`, `migrations.py`, `security.py`, `admin.py` and `i18n.py`.
- `templates/` holds the Jinja pages and `static/js/` the page scripts.
- `translations/` holds one `messages.po` per language and the `messages.pot` template.
- `docs/` holds the API guide (`api.md`), the generated `openapi.json` and the calendar feed guide.
- `tests/` holds the pytest suite.

## Running it

- To customise a local instance, copy `config-example.yml` to `config.yml` (it is gitignored). Without it the app runs on the defaults from `config-example.yml`, and the tests write their own temporary config.
- With Docker: `docker compose up -d`. To try local code changes, build the image from the checkout (for example `docker compose build` with `build: .` switched on for your run), but never commit that change to `compose.yml` (see below).
- Without Docker: `pip install -r requirements-dev.txt`, `npm install`, `npm run build:css`, then `python run.py`. Set `FLASK_DEBUG=1` for auto-reload.
- The Docker image compiles the translations itself. Outside Docker, run `flask translations compile` before testing a language other than English, or the pages stay in English.

## Tests

- Run them with `python -m pytest`. CI runs the same command on every pull request (`.github/workflows/tests.yml`).
- Each test file builds its own app with `create_app({...})` and an in-memory database (`'SQLALCHEMY_DATABASE_URI': 'sqlite://'`). Follow that pattern; never point a test at a real `data/app.db`.
- Changes to permissions, money calculations, startup migrations or anything security related should come with tests.
- If a test already fails on `main`, it is not yours to fix in an unrelated PR. Make sure your change adds no new failures.

## Upgrades must not break existing installs

- There is no Alembic. `app/migrations.py` upgrades existing databases at startup.
- A new column on an existing table needs both the model field and an entry in `COLUMNS` or `LATE_COLUMNS` in `app/migrations.py`, with a default that makes sense for old rows. Without it, existing installs crash with "no such column".
- A new table needs a `CREATE TABLE IF NOT EXISTS` in `TABLES`, or must be safe to create through `db.create_all()`.
- Migrations must be additive and safe to run again. Do not rename, drop or change the type of a column.
- A new `config.yml` key needs a default in code and an entry in `config-example.yml`. An existing `config.yml` without the key must keep working.

## Text and translations

- Every piece of text a user sees is translatable. In Python and templates wrap it in `_()`, or `ngettext()` for counts. In `static/js` use `t('text', {params})`, `th()` when the result goes into an HTML string, `tn(one, other, count)` for counts and `tc('context', 'text')` when one English word needs different translations.
- A sentence with a name, number or link in it is one string with a placeholder, never pieces glued together, so each language can put the value where its grammar needs it.
- After adding or changing text, run `flask translations update` to refresh `messages.pot` and every `messages.po`. Leave new `msgstr` entries empty unless you are a translator for that language; untranslated text falls back to English.
- The compiled `.mo` files are build output and are not committed.
- What people type into HomeHub (notes, chores, names) is never translated.

### Translation pull requests

These follow the rules in issue #81:

- Comment on #81 with your language before starting, so two people do not translate the same one.
- The PR changes only `translations/<code>/LC_MESSAGES/messages.po`, adds that language's row to the Supported languages table in the README, and, only for a script that is not Latin, Cyrillic or Greek, adds one line to `LANGUAGE_FONTS` in `app/i18n.py`.
- Do not edit `config-example.yml` or the config sample in the README.
- Write "Part of #81" in the description, not "Closes #81".
- Keep placeholders such as `%(name)s` and `{count}` exactly as they are, and add no links or HTML. `tests/test_i18n.py` checks both.
- Partial translations are welcome. Right-to-left languages are not supported yet.

## API documentation

`docs/openapi.json` is generated from the API's operation registry, and a test fails when it is out of date. After any change to `/api/v1` operations, run `python -m app.api.openapi` and commit the regenerated file. Update `docs/api.md` when the behaviour it describes changes.

## Security habits

HomeHub is meant for a trusted network, but a page on another website, a crafted link or one household member can still reach it. Keep these in place:

- **Outbound fetches.** Any server-side request to a URL a user supplied goes through `is_url_safe_for_fetch` in `app/security.py`, which refuses anything that is not http(s) or that resolves to a private, loopback or link-local address. It checks only the URL it is given, so a client that follows redirects on its own could still be sent to an internal address. Turn automatic redirects off, or check each new destination with `is_url_safe_for_fetch` before following it. Do not add a fetch that skips these checks.
- **Cross-site requests.** There are no per-form CSRF tokens. Instead, the before-request hook in `app/blueprints/auth.py` refuses any POST, PUT, PATCH or DELETE that the browser says came from another site (`Sec-Fetch-Site`, or `Origin` on older browsers). Keep that check ahead of everything else, and never change data from a GET request. `tests/test_cross_site.py` covers it.
- **User content.** Plain text goes through `sanitize_text` and rich text through `sanitize_html` before it is stored. Jinja autoescaping is on; do not add `|safe` unless the value was cleaned with `sanitize_html`. In page scripts, use `th()` or escape values before putting them into HTML strings.
- **Links.** A link a user enters must be http(s) (`is_http_url`). Refuse `javascript:` and other schemes when saving, and do not render old stored values that fail the check as clickable links.
- **Uploads.** Filenames go through `secure_filename` and files are served with `send_from_directory`, so a name cannot escape its folder.
- **Permissions.** Admin checks go through `is_admin(user)` in `app/admin.py`; never compare a name to `admin_name` or `'Administrator'` directly. Editing or deleting someone's item goes through `can_modify(user, <owner field>)` in the same file, not a hand-written `is_admin(user) or user == owner`: the helper refuses a locked session that names the admin to reach an admin-owned record. API tokens only open `/api/v1` and only what their scopes allow.
- **Secrets.** Passwords, hashes, API tokens and `SECRET_KEY` are never logged, flashed, rendered or returned in JSON, and password checks fail closed. The one exception is a new or regenerated API token: the admin page shows it once, in the response that creates it, so it can be copied, because HomeHub keeps only a fingerprint. That response must not be cached (`Cache-Control: no-store`), and the token must never appear again after it.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Front end

- Tailwind 3 with `darkMode: 'class'`. New UI needs `dark:` variants matching the markup around it and must work at phone width.
- `static/output.css` is built by `npm run build:css` and is not committed.

## Files that must not be committed

- `compose.yml` is the published config and points at `ghcr.io/surajverma/homehub:latest`. Do not commit a switch to a local build (such as uncommenting `build: .`) or other local edits to it.
- `config.yml`, `.env`, `data/`, `uploads/`, `media/`, `pdfs/`, compiled `.mo` files and `static/output.css` are local data or build output.

## Reviewing a change

The full checklist is in `.github/skills/code-review/SKILL.md`. In short, look for:

- a new column without a migration entry, or a migration that is not additive;
- a route that changes someone's data without `can_modify`, or an admin check that bypasses `is_admin`;
- unsanitised input, a new `|safe`, an unchecked outbound fetch or an unchecked link;
- money split with plain float division instead of the existing split helper in `app/blueprints/expenses.py`, so the parts no longer add up to the total;
- edits to recurring items that rewrite past occurrences by accident;
- new user-facing text that is not wrapped for translation, or an API change without a regenerated `docs/openapi.json`;
- `Model.query.get(id)` in new code, where `db.session.get(Model, id)` is preferred;
- any of the files listed above in the diff.

Lead each comment with the concrete failure (what input or state goes wrong, and what happens), put it on the line that causes it, and mark optional suggestions as optional. Skip style nits where the surrounding code already does the same thing.
