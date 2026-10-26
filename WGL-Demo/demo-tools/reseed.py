"""Moves the demo seed dates forward so the seed keeps its original relationship to 'today'.

    python reseed.py            dry run (prints the changes, writes nothing)
    python reseed.py --apply    backs up the touched documents, then writes only the date fields (updateMask) plus _seed_anchor_date
    python reseed.py --restore  writes the backed-up values back

Each document shifts by (today - its _seed_anchor_date) days. Technician slots shift by whole weeks only, so their dates stay on weekdays.
Re-running on the same day changes nothing, so it can be run before each demo.
Needs `gcloud auth application-default login` (project emvnzir-canada-song) and writes to the shared Firestore, which the production app also reads.
"""
import datetime, json, os, re, sys
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import fs

DOC_PREFIX = f"projects/{fs.PROJECT}/databases/{fs.DB}/documents/"
BACKUP = os.path.join(SP, "backups", "reseed_backup.json")
DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)")

PLAN = {   # collection -> (date fields, weekly_only)
    "wgl_customers": (["due_date", "shutoff_date", "bill_delivery_issue_date", "last_service_call_date"], False),
    "wgl_appointments": (["slot", "eta"], False),
    "wgl_outages": (["eta", "previous_eta"], False),
    "wgl_technician_slots": (["date"], True),
    "wgl_payments": (["paid_date"], False),
    "wgl_extensions": (["max_new_due_date", "promise_to_pay_max"], False),
    "wgl_rebate_claims": (["submitted_on"], False),
}


def eastern_today():
    now = datetime.datetime.now(datetime.timezone.utc)
    year = now.year
    march = datetime.date(year, 3, 1); dst_start = march + datetime.timedelta(days=(6 - march.weekday()) % 7 + 7)
    nov = datetime.date(year, 11, 1); dst_end = nov + datetime.timedelta(days=(6 - nov.weekday()) % 7)
    offset = -4 if dst_start <= (now.date()) < dst_end else -5
    return (now + datetime.timedelta(hours=offset)).date()


def shift_text(text, days):
    def one(m):
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) + datetime.timedelta(days=days)
        return d.isoformat()
    return DATE.sub(one, text, count=1)


def changes(today):
    out = []
    for coll, (fields, weekly) in PLAN.items():
        rows = fs.list_docs(coll)
        for r in rows:
            anchor = r.get("_seed_anchor_date")
            if not anchor:
                continue
            delta = (today - datetime.date.fromisoformat(str(anchor)[:10])).days
            if weekly:
                delta = (delta // 7) * 7
            if delta <= 0:
                continue
            new = {}
            for f in fields:
                v = r.get(f)
                if isinstance(v, str) and DATE.search(v):
                    new[f] = shift_text(v, delta)
            if not new:
                continue
            new["_seed_anchor_date"] = (datetime.date.fromisoformat(str(anchor)[:10]) + datetime.timedelta(days=delta)).isoformat()
            old = {k: r.get(k) for k in new}
            out.append({"coll": coll, "id": r["_id"], "delta": delta, "old": old, "new": new})
    return out


def commit(writes):
    for i in range(0, len(writes), 200):
        r = fs.call("POST", f"https://firestore.googleapis.com/v1/projects/{fs.PROJECT}/databases/{fs.DB}/documents:commit", {"writes": writes[i:i + 200]})
        if "error" in r:
            raise SystemExit(f"commit failed: {r}")


def update_write(coll, did, values):
    return {"update": {"name": f"{DOC_PREFIX}{coll}/{did}", "fields": {k: {"stringValue": v} for k, v in values.items()}},
            "updateMask": {"fieldPaths": list(values)}, "currentDocument": {"exists": True}}


if __name__ == "__main__":
    if "--restore" in sys.argv:
        saved = json.load(open(BACKUP, encoding="utf-8"))
        commit([update_write(c["coll"], c["id"], {k: ("" if v is None else v) for k, v in c["old"].items()}) for c in saved])
        print("restored", len(saved), "documents")
        sys.exit(0)
    today = eastern_today()
    plan = changes(today)
    by = {}
    for c in plan:
        by.setdefault(c["coll"], []).append(c)
    print("today (Eastern):", today)
    for coll, items in by.items():
        print(f"{coll}: {len(items)} documents, shifts {sorted({c['delta'] for c in items})} days")
        for c in items[:2]:
            print("    ", c["id"], c["old"], "->", c["new"])
    if "--apply" in sys.argv:
        old_blob = json.load(open(BACKUP, encoding="utf-8")) if os.path.exists(BACKUP) else []
        json.dump(old_blob + plan, open(BACKUP, "w", encoding="utf-8"))
        commit([update_write(c["coll"], c["id"], c["new"]) for c in plan])
        print("applied to", len(plan), "documents; backup in", BACKUP)
    else:
        print("dry run only: pass --apply to write")
