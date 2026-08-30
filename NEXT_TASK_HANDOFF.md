# FamilyGraph — handoff for the next task

## Where to work

- Upstream: `https://github.com/GriefKid/FamilyGraph.git`, branch `main`.
- `G:\projects\FamilyGraph` had a corrupted Git object database. The healthy clone used for all recent pushes is `C:\Users\legion\AppData\Local\Temp\FamilyGraph-repaired`.
- The canonical handoff is also committed to GitHub as commit `c7b7285` (`docs: add next task handoff`).

## Validate every change

```powershell
$env:USE_SQLITE='1'; python manage.py test
$env:USE_SQLITE='1'; python manage.py check
```

When the full suite exceeds the command timeout, run:

```powershell
$env:USE_SQLITE='1'; python manage.py test main.tests.RegistrationOnboardingTests main.tests.DashboardBriefingTests main.tests.PublicSocialTests main.tests.JournalMomentTests main.tests.JalaliPresentationTests main.tests.PersianDateExtractionTests main.tests.PersianExtractionScenarioTests --verbosity 1
$env:USE_SQLITE='1'; python manage.py test main.tests.ExtractionWorkflowTests main.tests.MemoryIntelligenceTests main.tests.RelationshipLifeCycleTests main.tests.PlatformQualityTests main.test_persian_chat main.test_navigation_cleanup --verbosity 1
$env:USE_SQLITE='1'; python manage.py check
```

The last split verification passed 76 tests (29 + 47).

## Product principles

FamilyGraph is private and Persian-first. Prioritize a low-effort first action, explainable/snoozable relationship support, strict tenant isolation, and production readiness for about 200 active users. Read `AGENTS.md` before changes.

## Work already complete

- Server-side, owner-scoped people search with Arabic/Persian character normalization, groups, attention filtering, pagination persistence, and recovery from empty filters.
- Merged people are hidden; important people can be pinned and sort first. Migrations through `0038_node_is_pinned.py`.
- Search normalization for command palette, graph, relationships, journal, and memory.
- Graph search: Escape clear, live result count, accessible label, exact Enter navigation.
- Daily recommendation snooze/feedback; notification cadence and explicit mark-all-read.
- Expiring/revocable person share links (`0034_sharelink.py`).
- Pending journal-image ownership (`0035_journalimage_owner.py`).
- Query indexes: active memory lookup (`0036_*`) and unread notification lookup (`0037_*`).
- PWA safe-update prompt, keyboard navigation and command-palette accessibility.
- Several internal write endpoints hardened with owner checks, POST, and CSRF.

Recent commits after the original handoff:

- `f898349` Persian journal search
- `a6708ac` Persian memory search
- `f3c7866` memory lookup index
- `ab7874a` unread notification index
- `26941e4` pin important people
- `5e8482a` pinned-person badges

## Recommended next tasks

1. Complete pinning: a `Pinned only` directory filter or dashboard pinned-people section.
2. Add graph focus/zoom for non-exact search results.
3. Improve journal filtering UX: result count and no-results recovery.
4. Audit remaining `@csrf_exempt` endpoints: retain only reviewed external integrations.
5. Improve weekly/monthly review using existing relationship, follow-up, event, and memory data.

## Required implementation discipline

1. Inspect existing callers before edits.
2. Scope user data to `request.user`/owner.
3. Protect writes with login, HTTP method decorators, and CSRF.
4. Add focused success, ownership, and invalid-input tests.
5. For models, generate and inspect a new migration; never edit applied migrations.
6. Do not commit `db.sqlite3`, `media/`, `django_cache/`, or other runtime data.
7. Test, check, commit, and push each completed stage.

## Useful test classes

- `DashboardBriefingTests`: directory, graph/search, pinning and ownership.
- `JournalMomentTests`: journal save/search.
- `MemoryIntelligenceTests`: memory facts/search.
- `RelationshipLifeCycleTests`: timeline, sharing and relationship flow.
- `PlatformQualityTests`: platform APIs and notifications.

Expected tests deliberately log some 404/400/403/429 responses.
