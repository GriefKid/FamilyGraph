# FamilyGraph — handoff for the next task

## Start here

- The upstream branch is `origin/main` on `https://github.com/GriefKid/FamilyGraph.git`.
- The original workspace at `G:\projects\FamilyGraph` previously had a corrupted Git object database. A healthy working clone was created at `C:\Users\legion\AppData\Local\Temp\FamilyGraph-repaired` and **all recent commits and pushes were made there**.
- Before editing, run `git status --short` and preserve unrelated changes.
- Use SQLite for local validation:

```powershell
$env:USE_SQLITE='1'; python manage.py test
$env:USE_SQLITE='1'; python manage.py check
```

If the complete suite exceeds the command-time limit, run these two verified groups instead:

```powershell
$env:USE_SQLITE='1'; python manage.py test main.tests.RegistrationOnboardingTests main.tests.DashboardBriefingTests main.tests.PublicSocialTests main.tests.JournalMomentTests main.tests.JalaliPresentationTests main.tests.PersianDateExtractionTests main.tests.PersianExtractionScenarioTests --verbosity 1
$env:USE_SQLITE='1'; python manage.py test main.tests.ExtractionWorkflowTests main.tests.MemoryIntelligenceTests main.tests.RelationshipLifeCycleTests main.tests.PlatformQualityTests main.test_persian_chat main.test_navigation_cleanup --verbosity 1
$env:USE_SQLITE='1'; python manage.py check
```

The latest full split run passed **76 tests** (29 + 47) before the most recent focused changes. Always rerun the narrow relevant tests, then the appropriate broad group before pushing meaningful work.

## Product direction

FamilyGraph is a private, Persian-first Django application for managing relationship graphs, memories, journals, events, follow-ups and AI-assisted insights. The product direction is:

1. Make the first useful action obvious and low-effort.
2. Help people maintain meaningful relationships without notification overload.
3. Keep all personal data tenant-scoped and private by default.
4. Make the product production-ready for roughly 200 active users before adding infrastructure that needs external services.

Read `AGENTS.md` before changing code. Its main requirements are ownership filtering, login/CSRF/method protection for state changes, timezone/Jalali correctness, N+1 avoidance, and no runtime-data commits.

## Recent work already pushed

Latest commit at handoff: `5e8482a` — `feat: show pinned people in the directory`.

Recent product milestones, in order:

- People directory now supports owner-scoped server-side search, Persian/Arabic letter normalization, group filtering, attention filtering, empty-filter recovery, and filter persistence across pagination.
- Merged people are hidden from normal directory search/listing.
- Important people can be pinned from their detail page; pinned people sort first and have a visible star in both directory views. Migration: `0038_node_is_pinned.py`.
- Command-palette, graph, relationship list, journal, and memory search now normalize `ی/ي` and `ک/ك`.
- Graph search has keyboard Escape-to-clear, accessible label, live result count, and Enter opens an exact person match.
- Relationship search has a live result count.
- Daily briefing supports snooze and “show less”; notifications have preferred cadence and explicit mark-all-read control.
- Public person cards have expiring/revocable share links. Migration: `0034_sharelink.py`.
- Pending journal images are owner-scoped. Migration: `0035_journalimage_owner.py` backfills owners from attached entries.
- Performance indexes added for `MemoryFact(owner, active)` and `Notification(user, is_read)`. Migrations: `0036_*`, `0037_*`.
- PWA prompts users to refresh safely when a new service worker is waiting.
- Keyboard accessibility was improved for primary navigation and command palette.
- Several internal state-changing endpoints were hardened to require POST/CSRF and enforce ownership (group assignment, event completion, chat clearing, quick person update, journal upload).

## Recommended next work, in priority order

1. **Pinned-people completion**
   - Add a “Pinned only” directory filter, or a compact pinned-people section on the dashboard.
   - Keep pinning owner-scoped and do not pin the root node automatically unless explicitly designed.

2. **Directory and graph usability at scale**
   - Add a focus/zoom action for non-exact graph search results, not only exact Enter navigation.
   - Consider server-side relationship list filtering if relationship counts grow beyond a few hundred.
   - Add accessible no-results feedback to graph and relationship search if it remains valuable after live counts.

3. **Journal workflow**
   - Add result counts/clearer no-results feedback to journal filters.
   - Review all journal write endpoints for CSRF/method decorators. Do not use `@csrf_exempt` for internal browser calls without a reviewed integration reason.
   - Keep pending images restricted by `owner=request.user` whenever attaching them to an entry.

4. **Production reliability**
   - Audit remaining `@csrf_exempt` endpoints in `main/views.py`; distinguish true external integrations from internal browser APIs.
   - Add targeted indexes only after examining actual repeated query shapes; inspect generated migrations and never edit applied migrations.
   - Add a deployment smoke-check/runbook section if configuration changes are made.

5. **Retention and user value**
   - Improve the weekly/monthly review from existing relationship, follow-up, memory, and event data.
   - Keep recommendations few, explainable, snoozable, and never send automatic messages on behalf of the user.

## Implementation checklist for every change

1. Inspect the existing view/template/API and callers first.
2. Filter every user-owned object by `request.user`/owner.
3. Use `@login_required`, an HTTP method decorator, and CSRF protection for writes.
4. Add or update focused tests, including success and ownership/invalid-input cases for APIs.
5. For model changes: run `makemigrations main`, inspect the migration, then run tests.
6. Run `python manage.py check` after tests.
7. `git add` only source, templates, tests, and migrations relevant to the change; do not add `db.sqlite3`, `media/`, `django_cache/`, or uploaded data.
8. Commit and `git push origin main` only after validation passes.

## Useful current test locations

- `main/tests.py`
  - `DashboardBriefingTests`: onboarding, directory, graph/search, pinning, ownership hardening.
  - `JournalMomentTests`: quick journal and journal search.
  - `MemoryIntelligenceTests`: memory facts/search/assistant behavior.
  - `RelationshipLifeCycleTests`: share links, timeline, relationship flow.
  - `PlatformQualityTests`: platform APIs, notification preferences, rate limiting, command palette.
- `main/test_navigation_cleanup.py`: navigation shell.
- `main/test_persian_chat.py`: Persian response quality.

## Important cautions

- The terminal console can render Persian source text as mojibake; do not mistake that for damaged UTF-8 source. Make edits carefully and verify through Django tests/templates.
- Expected test logs include `404`, `400`, `403`, and `429` warnings for deliberate negative-path tests.
- The user wants autonomous continuation: make a sensible scoped improvement, test it, commit it, push it, then continue to the next highest-value item without waiting for a “next” message.
