# FamilyGraph contributor guide

## Project overview

FamilyGraph is a Django application for managing personal relationship graphs, journals, events, social features, and AI-assisted insights. The project has one Django app, `main`, and uses PostgreSQL by default. A local SQLite database is supported only when `USE_SQLITE=1` is set.

## Layout

- `FamilyGraph/`: Django project configuration, root URLs, and ASGI/WSGI entry points.
- `main/models.py`: domain models and model-level integrity rules.
- `main/views*.py`: feature-specific views and JSON APIs; keep additions in the relevant module rather than growing `main/views.py` unnecessarily.
- `main/urls.py`: application routes. Add or update named routes when changing views.
- `main/forms.py`: Django forms for server-rendered CRUD flows.
- `main/migrations/`: schema migrations. Never edit applied migrations; generate a new migration for model changes.
- `templates/`: Django templates, organized by feature.
- `static/js/graph/`: graph-specific browser code.
- `media/`, `django_cache/`, and `db.sqlite3`: local runtime data; do not treat them as source files.

## Local commands

Run commands from the repository root. Set `USE_SQLITE=1` for local commands unless PostgreSQL is configured.

```powershell
$env:USE_SQLITE='1'; python manage.py check
$env:USE_SQLITE='1'; python manage.py test
$env:USE_SQLITE='1'; python manage.py makemigrations main
$env:USE_SQLITE='1'; python manage.py migrate
$env:USE_SQLITE='1'; python manage.py runserver
```

## Implementation conventions

- Preserve tenant isolation: data owned by users must always be filtered with `request.user` (or the equivalent owner) before reading, changing, or returning it.
- Protect authenticated pages and APIs with `@login_required` or `LoginRequiredMixin`. State-changing endpoints should use an appropriate HTTP-method decorator and CSRF protection; do not add `@csrf_exempt` unless an external integration makes it necessary and the risk has been reviewed.
- Parse JSON defensively and return clear `JsonResponse` errors for invalid input. Keep API response shapes compatible with existing frontend callers.
- Put cross-model constraints in model validation/save logic as well as forms when programmatic writes can occur.
- Use `django.utils.timezone` rather than naive datetimes; the product timezone is `Asia/Tehran`. Use `main.utils_jalali` for Jalali date presentation.
- Keep templates escaped by default. Avoid inserting user-controlled content via `innerHTML`; use existing escaping helpers or DOM APIs when dynamic HTML is required.
- Use `select_related`/`prefetch_related` or batched queries in views that render lists or graph data to avoid N+1 queries.
- Do not hardcode secrets, API keys, or database credentials. Configuration comes from environment variables and optional `.env` loading.

## Verification

For code changes, run the narrowest relevant test first, then `python manage.py check`. For model changes, also generate and inspect the migration. When a change affects an authenticated API, verify ownership boundaries and both success and invalid-input responses.

## Scope discipline

Keep changes focused. Do not modify generated cache files, uploaded media, the development SQLite database, or unrelated migrations. Preserve existing user changes in the worktree.
