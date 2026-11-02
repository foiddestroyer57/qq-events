#!/usr/bin/env python3
"""Runs on GitHub every ~5 minutes: reads new brochures from Drive, extracts details with Gemini and
publishes them straight into events.json. Delete a brochure from the Drive folder and its event disappears."""
import os, re, time, json, datetime
import update_events as u
import forms_helper as fh, compress_helper as chelp

MAX_BYTES = 15_000_000                  # bigger brochures are shrunk before Gemini reads them

EVENTS, REVIEW = os.path.join(u.HERE, "events.json"), os.path.join(u.HERE, "needs_review.json")
OVERRIDES = os.path.join(u.HERE, "overrides.json")      # written by the admin Google Form (Apps Script): {"<event id>": {"date": "2026-12-01", ...}}
EDITABLE = {"name": 120, "date": 10, "venue": 200, "classes": 120, "fee": 120, "deadline": 10, "registerOpens": 10, "description": 1500}

def save_if_changed(path, data):
    new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if os.path.exists(path) and open(path, encoding="utf-8").read() == new: return False
    open(path, "w", encoding="utf-8", newline="\n").write(new); return True

def apply_overrides(events):
    """Admin edits win over the brochure. The brochure's own value is remembered in "_orig", so deleting an
    override puts the brochure value back, and re-reading a brochure never loses an admin edit."""
    ovs = u._load(OVERRIDES, {})
    if not isinstance(ovs, dict): ovs = {}
    for ev in events.values():
        raw = ovs.get(ev["id"]); raw = raw if isinstance(raw, dict) else {}
        ov = {}
        for k, v in raw.items():
            if k not in EDITABLE or not isinstance(v, str) or not v.strip(): continue
            v = v.strip()[:EDITABLE[k]]
            if k in ("date", "deadline", "registerOpens") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): continue
            ov[k] = v
        orig = dict(ev.get("_orig") or {})
        for k in [k for k in orig if k not in ov]: ev[k] = orig.pop(k)            # override removed -> brochure value comes back
        for k, v in ov.items():
            if k not in orig: orig[k] = ev.get(k, "")
            if ev.get(k) != v: print("OVERRIDE %s: %s -> %s" % (ev["name"], k, v))
            ev[k] = v
        if orig: ev["_orig"] = orig
        else: ev.pop("_orig", None)

def ensure_forms(events):
    today = datetime.date.today().isoformat()
    todo = [e for e in events.values() if not e.get("formId") and (not e["date"] or e["date"] >= today)][:5]      # a few per run
    if not todo: print("forms: nothing to create (every upcoming event already has a form)"); return
    try: fs, ds = fh.services()
    except Exception as e: print("FORMS UNAVAILABLE:", str(e)[:200]); return
    qs = u._load(os.path.join(u.HERE, "form_questions.json"), fh.DEFAULT_QUESTIONS)
    for ev in todo:
        try: link, fid = fh.create_form(fs, ds, ev, qs, os.environ.get("ADMIN_EMAIL", ""))
        except Exception as e: print("FORM FAILED for %s: %s" % (ev["name"], str(e)[:250])); continue
        ev["register"], ev["formId"] = link, fid; print("FORM CREATED for", ev["name"])

def reset_forms(events):
    """Tick 'reset_forms' when running the workflow by hand: drops every stored form link so brand-new forms are made."""
    if os.environ.get("RESET_FORMS", "").strip().lower() != "true": return 0
    n = 0
    for ev in events.values():
        if ev.pop("formId", None):
            ev.pop("register", None); ev.pop("organiserLink", None); n += 1
    print("RESET: removed the old form link from %d event(s) - new forms are made below" % n)
    return n

def main():
    svc, client = u.drive(), u.gemini()
    raw_id = os.environ["DRIVE_FOLDER_ID"].strip(); m = re.search(r"folders/([\w-]+)", raw_id); fid = m.group(1) if m else raw_id.split("?")[0].strip()
    try: svc.files().get(fileId=fid, fields="id,name").execute()
    except Exception as e: raise SystemExit("CANNOT OPEN THE DRIVE FOLDER. Share it with the brochure-reader service account (Viewer) or fix the DRIVE_FOLDER_ID secret. (%s)" % str(e)[:150])
    files = u.list_brochures(svc, fid); live = {f["id"] for f in files}
    print("Found %d brochure(s) in the Drive folder: %s" % (len(files), ", ".join(f["name"] for f in files) or "NONE - put a PDF or image directly inside the folder"))
    state = u._load(u.STATE, {}); events = {e["id"]: e for e in u._load(EVENTS, {"events": []})["events"]}
    review = {r["file"]: r for r in u._load(REVIEW, [])}
    for k in [k for k, r in review.items() if "no date found" in r["reason"] and "no event name found" not in r["reason"]]:
        review.pop(k); state.pop(k, None)                      # older "missing date" entries get read again and are now published
    for f in files:
        if state.get(f["id"]) == f["modifiedTime"]: continue
        try:
            data, mime = u.download(svc, f["id"]), f["mimeType"]
            if len(data) > MAX_BYTES:
                before = len(data); data, mime = chelp.shrink(data, mime, MAX_BYTES)
                print("COMPRESSED %s: %.1f MB -> %.1f MB" % (f["name"], before / 1e6, len(data) / 1e6))
            ev = u.clean(u.extract(client, data, mime), f)
        except chelp.TooBig as e:
            review[f["id"]] = {"file": f["id"], "name": f["name"], "reason": "file is over 15 MB and could not be compressed: " + str(e)}; state[f["id"]] = f["modifiedTime"]
            print("TOO BIG %s: %s" % (f["name"], e)); continue
        except Exception as e: print("WILL RETRY %s: %s" % (f["name"], str(e)[:200])); continue      # not marked done -> retried next run
        old = next((v for v in events.values() if v.get("source") == f["id"]), None)
        events = {k: v for k, v in events.items() if v.get("source") != f["id"]}; state[f["id"]] = f["modifiedTime"]
        if not ev["name"]:
            review[f["id"]] = {"file": f["id"], "name": f["name"], "reason": "no event name found - check the brochure"}; print("NEEDS REVIEW", f["name"]); continue
        if not ev["date"]: print("NO DATE in %s - publishing it as 'date will be announced soon'" % f["name"])
        review.pop(f["id"], None); ev = {k: v for k, v in ev.items() if not k.startswith("_")}; ev["source"] = f["id"]
        if old and old.get("formId"): ev.update({k: old[k] for k in ("formId", "register") if k in old})   # keep the existing form on a re-read
        events[ev["id"]] = ev
        print("PUBLISHED %s (%s)" % (ev["name"], ev["date"])); time.sleep(5)
    apply_overrides(events)
    reset_forms(events)
    ensure_forms(events)
    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    events = {k: v for k, v in events.items() if v.get("source") in live and (not v["date"] or v["date"] >= cutoff)}   # deleted brochure or long-past event -> removed
    review = {k: v for k, v in review.items() if k in live}
    ch = [save_if_changed(EVENTS, {"events": sorted(events.values(), key=lambda e: e["date"] or "9999-12-31")}), save_if_changed(REVIEW, list(review.values())), save_if_changed(u.STATE, state)]
    print("files changed" if any(ch) else "nothing new")

if __name__ == "__main__": main()
