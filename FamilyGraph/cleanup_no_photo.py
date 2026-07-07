# -*- coding: utf-8 -*-
"""
cleanup_no_photo.py — پاک کردن همه‌ی نودهای بدون عکس + تمام داده‌های وابسته

اجرا:  python cleanup_no_photo.py
  1. اول از db.sqlite3 بکاپ می‌گیره
  2. لیست نودهای بدون عکس رو نشون می‌ده
  3. تأیید می‌گیره، بعد پاک می‌کنه:
     یال‌ها + تاریخچه قدرت، تعامل‌ها، موضوعات باز، قرض‌ها، دایره نزدیکی،
     اطلاعات، عضویت در رویدادها/ژورنال/گروه‌ها و خود نود
  ⚠️ نود root (من) هرگز پاک نمی‌شه حتی اگه عکس نداشته باشه.
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'FamilyGraph', 'db.sqlite3')
if not os.path.exists(DB_PATH):
    print(f"❌ db.sqlite3 پیدا نشد: {DB_PATH}")
    sys.exit(1)

CHUNK = 400


def chunks(lst, n=CHUNK):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def run():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── root ها مصون‌ان — جدول کاربر رو داینامیک پیدا کن ──
    # (بسته به تاریخچه‌ی migration ممکنه main_user یا auth_user یا … باشه)
    roots = set()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    for t in all_tables:
        try:
            cur.execute(f"PRAGMA table_info({t})")
            cols = {r[1] for r in cur.fetchall()}
            if 'root_node_id' in cols:
                cur.execute(f"SELECT root_node_id FROM {t} WHERE root_node_id IS NOT NULL")
                roots |= {r[0] for r in cur.fetchall()}
        except Exception:
            pass
    if not roots:
        print("⚠️  نود root پیدا نشد — با احتیاط ادامه می‌دیم (هیچ نودی مصون نیست).")

    # ── سلامت‌سنجی: جدول نودها باید باشه ──
    if 'main_node' not in all_tables:
        print("\n❌ جدول main_node توی این فایل نیست! یعنی این db.sqlite3 اونی نیست که سایت استفاده می‌کنه.")
        print(f"   فایل بررسی‌شده: {DB_PATH}")
        print("   جدول‌های موجود در این فایل:")
        for t in sorted(all_tables):
            print(f"     - {t}")
        conn.close()
        sys.exit(1)

    # ── نودهای بدون عکس ──
    cur.execute("SELECT id, username, name FROM main_node WHERE picture IS NULL OR picture = ''")
    rows = [r for r in cur.fetchall() if r[0] not in roots]

    if not rows:
        print("✅ هیچ نود بدون عکسی (به جز root) وجود نداره — کاری نیست.")
        conn.close()
        return

    print(f"\n🔍 {len(rows)} نود بدون عکس پیدا شد:")
    for _id, uname, nm in rows[:60]:
        print(f"   - #{_id}  {uname or nm or '؟'}")
    if len(rows) > 60:
        print(f"   ... و {len(rows) - 60} تای دیگه")
    if roots:
        print(f"\n🛡  نود root (من) مصونه و پاک نمی‌شه.")

    ans = input(f"\n⚠️  همه‌ی این {len(rows)} نود با یال‌ها و کل داده‌هاشون پاک بشن؟ (y/n): ").strip().lower()
    if ans not in ('y', 'yes', 'بله'):
        print("لغو شد — هیچی پاک نشد.")
        conn.close()
        return

    # ── بکاپ ──
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = DB_PATH.replace('db.sqlite3', f'db.backup-{stamp}.sqlite3')
    shutil.copy2(DB_PATH, backup)
    print(f"💾 بکاپ گرفته شد: {os.path.basename(backup)}")

    ids = [r[0] for r in rows]
    deleted = {}

    def exists(table):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return cur.fetchone() is not None

    def del_in(table, col):
        if not exists(table):
            return 0
        total = 0
        for ch in chunks(ids):
            ph = ','.join('?' * len(ch))
            cur.execute(f"DELETE FROM {table} WHERE {col} IN ({ph})", ch)
            total += cur.rowcount
        return total

    # ── ۱) یال‌ها (+ تاریخچه قدرت) ──
    rel_ids = []
    if exists('main_relationship'):
        for ch in chunks(ids):
            ph = ','.join('?' * len(ch))
            cur.execute(f"SELECT id FROM main_relationship WHERE source_id IN ({ph}) OR target_id IN ({ph})",
                        ch + ch)
            rel_ids += [r[0] for r in cur.fetchall()]
    rel_ids = list(set(rel_ids))
    if exists('main_relationshipstrengthhistory') and rel_ids:
        t = 0
        for ch in chunks(rel_ids):
            ph = ','.join('?' * len(ch))
            cur.execute(f"DELETE FROM main_relationshipstrengthhistory WHERE relationship_id IN ({ph})", ch)
            t += cur.rowcount
        deleted['تاریخچه قدرت'] = t
    t = 0
    for ch in chunks(rel_ids):
        ph = ','.join('?' * len(ch))
        cur.execute(f"DELETE FROM main_relationship WHERE id IN ({ph})", ch)
        t += cur.rowcount
    deleted['یال'] = t

    # ── ۲) داده‌های وابسته ──
    deleted['تعامل']        = del_in('main_interaction', 'node_id')
    deleted['موضوع باز']    = del_in('main_followup', 'node_id')
    deleted['قرض/طلب']      = del_in('main_debt', 'node_id')
    deleted['دایره نزدیکی'] = del_in('main_nodecloseness', 'node_id')
    deleted['اطلاعات']      = del_in('main_information', 'node_id')
    deleted['عضویت رویداد'] = del_in('main_event_participants', 'node_id')
    deleted['ذکر در ژورنال'] = del_in('main_journalentry_mentioned_nodes', 'node_id')
    deleted['عضویت گروه']   = del_in('main_node_groups', 'node_id')

    # هشدارها: فقط رفرنس آزاد می‌شه
    if exists('main_alertaction'):
        for ch in chunks(ids):
            ph = ','.join('?' * len(ch))
            cur.execute(f"UPDATE main_alertaction SET node_id = NULL WHERE node_id IN ({ph})", ch)

    # ── ۳) خود نودها ──
    deleted['نود'] = del_in('main_node', 'id')

    conn.commit()
    conn.close()

    print("\n✅ پاک‌سازی انجام شد:")
    for k, v in deleted.items():
        if v:
            print(f"   ✔ {v} {k}")
    print(f"\n💾 اگه پشیمون شدی، بکاپ اینجاست: {os.path.basename(backup)}")
    print("سرور رو ریفرش/ریستارت کن تا گراف جدید رو ببینی.")


if __name__ == '__main__':
    print("=== cleanup_no_photo.py — حذف نودهای بدون عکس ===")
    run()
    input("\n[Enter] برای خروج...")
