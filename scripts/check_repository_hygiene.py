"""Fail CI when private runtime data or secret files are tracked by Git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath


BLOCKED_PREFIXES = (
    "media/",
    "django_cache/",
    "ollama/models/",
    "staticfiles/",
)
BLOCKED_NAMES = {
    ".env",
    "backup_data.json",
    "db.sqlite3",
}
BLOCKED_SUFFIXES = {
    ".fgb",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    ]


def blocked_reason(path: str) -> str | None:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    if lowered.startswith(BLOCKED_PREFIXES):
        return "runtime/user-upload directory"
    if name in BLOCKED_NAMES or (name.startswith("backup_data") and name.endswith(".json")):
        return "database or personal-data backup"
    if PurePosixPath(lowered).suffix in BLOCKED_SUFFIXES:
        return "secret or encrypted backup file"
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return "environment secrets file"
    return None


def main() -> int:
    blocked = [(path, blocked_reason(path)) for path in tracked_files()]
    blocked = [(path, reason) for path, reason in blocked if reason]
    if not blocked:
        print("Repository hygiene check passed.")
        return 0

    print("Private runtime data must not be tracked:", file=sys.stderr)
    for path, reason in blocked:
        print(f"- {path}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
