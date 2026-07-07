"""
patch_db.py — Run: python patch_db.py
ستون‌ها و جدول‌های جدید رو به database اضافه می‌کنه بدون نیاز به django migrate
  - V3.5: ستون‌های event (event_time + reminders)
  - V4:   جدول‌های main_interaction و main_nodecloseness (تعامل‌ها + دایره نزدیکی)
"""
import sqlite3
import os
import sys

# پیدا کردن db.sqlite3
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'FamilyGraph', 'db.sqlite3')
if not os.path.exists(DB_PATH):
    print(f"❌ db.sqlite3 پیدا نشد در: {DB_PATH}")
    sys.exit(1)

# (جدول، ستون، تعریف)
COLUMNS_TO_ADD = [
    ("main_event", "event_time",          "time NULL"),
    ("main_event", "reminder_sent_7d",    "bool NOT NULL DEFAULT 0"),
    ("main_event", "reminder_sent_1d",    "bool NOT NULL DEFAULT 0"),
    ("main_event", "reminder_sent_3h",    "bool NOT NULL DEFAULT 0"),
    ("main_event", "post_event_prompted", "bool NOT NULL DEFAULT 0"),
]

# V4: جدول‌های جدید — (نام جدول، SQL ساخت، SQLهای index)
TABLES_TO_ADD = [
    (
        "main_interaction",
        """CREATE TABLE "main_interaction" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "kind" varchar(15) NOT NULL DEFAULT 'call',
            "date" date NOT NULL,
            "feeling" smallint NOT NULL DEFAULT 0,
            "note" varchar(300) NOT NULL DEFAULT '',
            "created_at" datetime NOT NULL,
            "node_id" bigint NOT NULL REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        ['CREATE INDEX "ix_inter_owner_node_date" ON "main_interaction" '
         '("owner_id", "node_id", "date" DESC)'],
    ),
    (
        "main_nodecloseness",
        """CREATE TABLE "main_nodecloseness" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "tier" varchar(15) NOT NULL,
            "node_id" bigint NOT NULL UNIQUE REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        [],
    ),
    (
        "main_debt",
        """CREATE TABLE "main_debt" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "direction" varchar(10) NOT NULL,
            "amount" bigint NOT NULL,
            "paid" bigint NOT NULL DEFAULT 0,
            "currency" varchar(20) NOT NULL DEFAULT 'تومان',
            "date" date NOT NULL,
            "due_date" date NULL,
            "note" varchar(300) NOT NULL DEFAULT '',
            "settled" bool NOT NULL DEFAULT 0,
            "settled_at" datetime NULL,
            "created_at" datetime NOT NULL,
            "node_id" bigint NOT NULL REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        ['CREATE INDEX "ix_debt_owner_node_settled" ON "main_debt" ("owner_id", "node_id", "settled")'],
    ),
    (
        "main_chatmessage",
        """CREATE TABLE "main_chatmessage" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "role" varchar(10) NOT NULL,
            "content" text NOT NULL,
            "created_at" datetime NOT NULL,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        [],
    ),
    (
        "main_lifeevent",
        """CREATE TABLE "main_lifeevent" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "kind" varchar(15) NOT NULL,
            "title" varchar(200) NOT NULL DEFAULT '',
            "date" date NOT NULL,
            "archived" bool NOT NULL DEFAULT 0,
            "created_at" datetime NOT NULL,
            "node_id" bigint NOT NULL REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        [],
    ),
    (
        "main_relationshipgoal",
        """CREATE TABLE "main_relationshipgoal" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "text" varchar(300) NOT NULL,
            "status" varchar(10) NOT NULL DEFAULT 'active',
            "baseline_score" integer NULL,
            "created_at" datetime NOT NULL,
            "closed_at" datetime NULL,
            "node_id" bigint NOT NULL REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        [],
    ),
    (
        "main_followup",
        """CREATE TABLE "main_followup" (
            "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
            "text" varchar(300) NOT NULL,
            "due_date" date NULL,
            "done" bool NOT NULL DEFAULT 0,
            "done_at" datetime NULL,
            "created_at" datetime NOT NULL,
            "node_id" bigint NOT NULL REFERENCES "main_node" ("id") DEFERRABLE INITIALLY DEFERRED,
            "owner_id" bigint NULL REFERENCES "main_user" ("id") DEFERRABLE INITIALLY DEFERRED
        )""",
        ['CREATE INDEX "ix_followup_owner_node" ON "main_followup" ("owner_id", "node_id", "done")'],
    ),
]

def _record_migration(cur, name):
    """ثبت migration record تا django migrate دوباره سعی نکنه."""
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='django_migrations'")
    if not cur.fetchone():
        return
    cur.execute("SELECT COUNT(*) FROM django_migrations WHERE app='main' AND name=?", (name,))
    if cur.fetchone()[0] == 0:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        cur.execute(
            "INSERT INTO django_migrations (app, name, applied) VALUES (?, ?, ?)",
            ('main', name, now)
        )
        print(f"  ✔ migration record ثبت شد: {name}")
    else:
        print(f"  ✓ migration record قبلاً ثبت بود: {name}")

def patch():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # بررسی وجود جدول main_event
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='main_event'")
    if not cur.fetchone():
        print("❌ جدول main_event پیدا نشد. ممکنه DB هنوز initialize نشده.")
        conn.close()
        sys.exit(1)

    added = []
    # ── ستون‌های جدید ──
    col_cache = {}
    for table, col, typedef in COLUMNS_TO_ADD:
        if table not in col_cache:
            cur.execute(f"PRAGMA table_info({table})")
            col_cache[table] = {row[1] for row in cur.fetchall()}
        if col not in col_cache[table]:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            added.append(f"{table}.{col}")
            print(f"  ✔ ستون اضافه شد: {table}.{col}")
        else:
            print(f"  ✓ قبلا وجود داشت: {table}.{col}")

    # ── V4: جدول‌های جدید ──
    for tname, tsql, indexes in TABLES_TO_ADD:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tname,))
        if not cur.fetchone():
            cur.execute(tsql)
            for isql in indexes:
                cur.execute(isql)
            added.append(f"{tname} (جدول)")
            print(f"  ✔ جدول ساخته شد: {tname}")
        else:
            print(f"  ✓ جدول قبلاً وجود داشت: {tname}")

    # ── migration records ──
    _record_migration(cur, '0005_event_time_reminders')
    _record_migration(cur, '0006_interaction_closeness')
    _record_migration(cur, '0007_followup')
    # این migration فقط metadata است (ordering + choices) — SQL نداره، رکوردش کافیه
    _record_migration(cur, '0008_alter_event_options_alter_interaction_kind')
    _record_migration(cur, '0008_debt')
    _record_migration(cur, '0009_chatmessage')
    _record_migration(cur, '0010_lifeevent_goal')

    conn.commit()
    conn.close()

    if added:
        print(f"\n✅ {len(added)} تغییر اعمال شد. سرور رو ریستارت کن.")
    else:
        print("\n✅ همه‌چیز قبلاً وجود داشت — هیچ تغییری نیاز نیست.")

if __name__ == '__main__':
    print("=== patch_db.py — آپدیت database (event + تعامل‌ها + دایره نزدیکی) ===\n")
    patch()
    print("\nاگه سرور در حال اجرا بود، Ctrl+C بزن و دوباره runserver کن.")
    input("\n[Enter] برای خروج...")
