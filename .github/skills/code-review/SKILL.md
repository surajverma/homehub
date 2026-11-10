---
name: code-review
description: Review pull requests to HomeHub, a self-hosted Flask + SQLAlchemy + Jinja + Tailwind 3 family app run in Docker. Use when reviewing any PR or diff in this repository.
---

# HomeHub code review

HomeHub is a small self-hosted family app (Flask, SQLAlchemy on SQLite, Jinja templates, Tailwind 3, Docker). It runs on a home network for one household, and people upgrade it by pulling a new image over their existing data.

**The rules are in [AGENTS.md](../../../AGENTS.md).** Read it first and review against it; this file only adds how to review. Do not keep a second copy of the rules here.

## What to look at first

Most real problems in HomeHub pull requests come from these, in this order:

1. **Existing installs.** A new column without an entry in `app/migrations.py`, a migration that is not additive or not safe to run twice (beyond the table-rebuild exception in AGENTS.md), anything that would make people run a command to upgrade, or a migration tested only through `create_app` (which skips migrations in tests).
2. **Permissions.** An owner-only edit or delete without `can_modify`, an admin check that does not go through `is_admin`, an empty user that could count as an owner, an upgrade that widens an existing API token.
3. **Deletes.** User content deleted for good instead of going through the shared trash, or an API/MCP operation that deletes for good.
4. **Input and output.** Unsanitised input, a new `|safe`, an unchecked outbound fetch or link, exception text or a secret (including an Undo token) reaching a page or a JSON answer.
5. **Money and recurring items.** An amount split with plain float division instead of the split helper, or an edit that rewrites past occurrences by accident.
6. **Text and docs.** User-facing text not wrapped for translation; an `/api/v1` change without a regenerated `docs/openapi.json` or an updated `docs/api.md`.
7. **Files.** `compose.yml` edits, `config.yml`, `data/`, `static/output.css` or other local files in the diff.

Judge a pull request by whether it adds test failures compared with its base branch.

## How to write comments

- Lead with the concrete failure: what input or state goes wrong, and what happens.
- One comment per issue, on the line that causes it.
- Mark optional suggestions clearly as optional.
- Skip style nits where the surrounding code already does the same thing.
