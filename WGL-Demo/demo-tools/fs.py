"""Small Firestore REST helper (read by default). Token from application-default credentials."""
import json, subprocess, urllib.request, urllib.error

PROJECT = "emvnzir-canada-song"
DB = "acn-bank-fs-db"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/{DB}/documents"
_token = None


def token():
    global _token
    if _token is None:
        r = subprocess.run("gcloud auth application-default print-access-token", shell=True, capture_output=True, text=True)
        _token = r.stdout.strip()
    return _token


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json", "x-goog-user-project": PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:400]}


def plain(v):
    if "stringValue" in v: return v["stringValue"]
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return v["doubleValue"]
    if "booleanValue" in v: return v["booleanValue"]
    if "timestampValue" in v: return v["timestampValue"]
    if "nullValue" in v: return None
    if "arrayValue" in v: return [plain(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: plain(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def list_docs(coll):
    out, page = [], ""
    while True:
        r = call("GET", f"{BASE}/{coll}?pageSize=300" + (f"&pageToken={page}" if page else ""))
        if "error" in r:
            return r
        for d in r.get("documents", []):
            out.append({"_id": d["name"].split("/")[-1], **{k: plain(v) for k, v in d.get("fields", {}).items()}})
        page = r.get("nextPageToken", "")
        if not page:
            return out
