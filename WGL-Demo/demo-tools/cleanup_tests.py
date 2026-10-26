"""Deletes the records that scripted test runs created, and nothing else.

Scripted runs use session ids of the form t-<10 hex chars>. Only documents whose id (or session_id field) matches that pattern are touched, in
wgl_sessions, wgl_session_events, wgl_handoffs, wgl_notifications and wgl_callback_tickets. Widget sessions (dfMessenger-...), uuid sessions,
the seed data, wgl_service_orders and wgl_counters are left alone.

    python cleanup_tests.py           dry run: counts only
    python cleanup_tests.py --apply   backs up the documents to cleanup_backup.json, then deletes them
"""
import json, os, re, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import fs

MINE = re.compile(r"^t-[0-9a-f]{10}(_|$)")
COLLS = ["wgl_sessions", "wgl_session_events", "wgl_handoffs", "wgl_notifications", "wgl_callback_tickets"]
PREFIX = f"projects/{fs.PROJECT}/databases/{fs.DB}/documents/"

targets = {}
for c in COLLS:
    rows = fs.list_docs(c)
    targets[c] = [r for r in rows if MINE.match(r["_id"]) or MINE.match(str(r.get("session_id", "")))]
    print(f"{c:24} {len(targets[c]):5} of my test documents")

if "--apply" in sys.argv:
    json.dump(targets, open(os.path.join(SP, "backups", "cleanup_backup.json"), "w", encoding="utf-8"), default=str)
    for c, rows in targets.items():
        writes = [{"delete": f"{PREFIX}{c}/{r['_id']}"} for r in rows]
        for i in range(0, len(writes), 200):
            r = fs.call("POST", f"https://firestore.googleapis.com/v1/projects/{fs.PROJECT}/databases/{fs.DB}/documents:commit", {"writes": writes[i:i + 200]})
            if "error" in r:
                raise SystemExit(f"delete failed in {c}: {r}")
        print("deleted", len(rows), "from", c)
else:
    print("dry run only: pass --apply to delete")
