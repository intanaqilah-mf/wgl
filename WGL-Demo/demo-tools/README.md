# Demo data tools

Small scripts for keeping the shared demo Firestore (`projects/emvnzir-canada-song`, database `acn-bank-fs-db`) fit for a demo. They need Python 3 and `gcloud auth application-default login`. Run them from this folder. Both default to a **dry run**; pass `--apply` to write. Backups go to `backups/` (not committed).

| Script | What it does |
|---|---|
| `reseed.py` | Moves the seed dates (due and shutoff dates, appointments, outage ETAs, technician slots, payment dates, extensions, rebate claims) forward so they keep their original relation to today. Billing history, meter reads and weather are left as they are. Slots move by whole weeks so they stay on weekdays. Safe to re-run on the same day. `--restore` writes the saved values back. |
| `cleanup_tests.py` | Deletes the records created by scripted test sessions (session ids `t-<10 hex>`): sessions, session events, handoffs, notifications and callback tickets. Widget and other users' sessions are never touched. |
| `fs.py` | Tiny Firestore REST helper the two scripts share. |

Both scripts write to the database the production app reads, so run them only when that is acceptable.

Run `python reseed.py --apply` before a demo (the seed drifts a day for every day that passes), then `python reseed.py` should report nothing to change.
