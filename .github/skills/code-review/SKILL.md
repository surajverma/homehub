---
name: code-review
description: Review pull requests to HomeHub, a self-hosted Flask + SQLAlchemy + Jinja + Tailwind 3 family app run in Docker. Use when reviewing any PR or diff in this repository.
---

# HomeHub code review

HomeHub is a small self-hosted family app (Flask, SQLAlchemy on SQLite, Jinja templates, Tailwind 3, Docker). It runs on a home network for one household, and people upgrade it by pulling a new image over their existing data. Review for real bugs and for breaks in the conventions below. Skip style nits where the surrounding code already does the same thing.

## Upgrades must not break existing installs

- There is no Alembic. Existing databases are upgraded at startup by `app/migrations.py`, which `create_app` calls. New columns go in its `COLUMNS` or `LATE_COLUMNS` lists.
- **A new column on an existing table needs both the model field and an entry in `COLUMNS` or `LATE_COLUMNS` in `app/migrations.py`**. Without it, existing installs crash with "no such column". Check that the default makes sense for old rows.
- A new table needs a `CREATE TABLE IF NOT EXISTS` in `TABLES` there, or must be safe to create through `db.create_all()`.
- Startup migrations must be idempotent and additive. Flag column renames, drops or type changes, since they break existing `data/app.db` files.
- New `config.yml` keys must have a default in code, and should be added to `config-example.yml`. An existing `config.yml` without the key must keep working.

## Permissions

- Family members pick their name from a switcher; there are no per-member accounts. The acting user arrives as a `user` form or query field and should go through `sanitize_text`.
- **Admin checks go through the single `is_admin(user)` helper in `app/admin.py`.** Flag code that compares a name to `admin_name`, `'Administrator'` or `'admin'` directly, or reads `admin_name` from config to decide permissions. A direct comparison bypasses whatever protection the helper adds.
- The usual permission shape is `is_admin(user) or user == <owner field>` (for example `creator` or `payer`). Flag a route that edits or deletes someone's data without an owner/admin check.
- Secrets (passwords, hashes, `SECRET_KEY`) must never be logged, flashed, rendered or returned in JSON. Password checks must fail closed.
- The site password in `config.yml` is a separate gate and must keep working.
- CSRF is disabled on purpose (`WTF_CSRF_ENABLED = False`). Don't ask for CSRF tokens on individual forms; do flag state changes made through GET requests.

## Input and output safety

- User text goes through `sanitize_text`, and rich text through `sanitize_html` (`app/security.py`), before it is stored.
- Server-side fetches of user-supplied URLs must go through `is_url_safe_for_fetch`.
- Jinja autoescaping is on. Flag new `|safe` unless the value was cleaned with `sanitize_html`.
- Uploaded filenames go through `secure_filename`, and files are served with `send_from_directory`, so a name can't escape its folder.

## Data and money

- Prefer `db.session.get(Model, id)` over the legacy `Model.query.get(id)` in new code.
- Money must add up exactly after rounding. Flag code that splits an amount with plain float division and rounds each part on its own; reuse the existing split helper in `app/blueprints/expenses.py` instead.
- Changes to recurring items (expenses, reminders, chores) must not rewrite past occurrences unless that is the stated intent.

## Front end

- Tailwind 3 with `darkMode: 'class'`. New UI should include `dark:` variants matching the neighbouring markup, and should work at phone width.
- `static/output.css` is built with `npm run build:css` and is gitignored. Flag a PR that commits it.

## Files that must not be committed

- `compose.yml` is the published config and points at `ghcr.io/surajverma/homehub:latest`. Flag any change that switches it to a local build (for example uncommenting `build: .`).
- `config.yml`, `.env`, `data/app.db`, `uploads/`, `media/` and `pdfs/` are local data. Flag any of them in a diff.

## Tests

- Tests are pytest files in `tests/`. Each file builds its own app with `create_app(test_config)` and an in-memory SQLite database (`'sqlite://'`). New tests should follow that pattern, not use a real database.
- Changes to permissions, money calculations or startup migrations should come with tests.
- The suite needs a `config.yml` in the repo root to start (copy `config-example.yml`).
- A few tests already fail on `main`. Judge a PR by whether it adds failures compared with its base branch, not by whether the whole suite is green.

## How to write comments

- Lead with the concrete failure: what input or state goes wrong, and what happens.
- One comment per issue, on the line that causes it.
- Mark optional suggestions clearly as optional.
