#!/usr/bin/env python3
"""Runs on GitHub every ~5 minutes: reads new brochures from Drive, extracts details with Gemini and
publishes them straight into events.json. Delete a brochure from the Drive folder and its event disappears."""
import os, re, time, json, datetime
import update_events as u

EVENTS, REVIEW = os.path.join(u.HERE, "events.json"), os.path.join(u.HERE, "needs_review.json")

def save_if_changed(path, data):
    new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if os.path.exists(path) and open(path, encoding="utf-8").read() == new: return False
    open(path, "w", encoding="utf-8", newline="\n").write(new); return True

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
        if int(f.get("size", 0)) > 15_000_000:
            review[f["id"]] = {"file": f["id"], "name": f["name"], "reason": "file is over 15 MB - compress it"}; state[f["id"]] = f["modifiedTime"]; continue
        try: ev = u.clean(u.extract(client, u.download(svc, f["id"]), f["mimeType"]), f)
        except Exception as e: print("WILL RETRY %s: %s" % (f["name"], str(e)[:200])); continue      # not marked done -> retried next run
        events = {k: v for k, v in events.items() if v.get("source") != f["id"]}; state[f["id"]] = f["modifiedTime"]
        if not ev["name"]:
            review[f["id"]] = {"file": f["id"], "name": f["name"], "reason": "no event name found - check the brochure"}; print("NEEDS REVIEW", f["name"]); continue
        if not ev["date"]: print("NO DATE in %s - publishing it as 'date will be announced soon'" % f["name"])
        review.pop(f["id"], None); ev = {k: v for k, v in ev.items() if not k.startswith("_")}; ev["source"] = f["id"]; events[ev["id"]] = ev
        print("PUBLISHED %s (%s)" % (ev["name"], ev["date"])); time.sleep(5)
    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    events = {k: v for k, v in events.items() if v.get("source") in live and (not v["date"] or v["date"] >= cutoff)}   # deleted brochure or long-past event -> removed
    review = {k: v for k, v in review.items() if k in live}
    ch = [save_if_changed(EVENTS, {"events": sorted(events.values(), key=lambda e: e["date"] or "9999-12-31")}), save_if_changed(REVIEW, list(review.values())), save_if_changed(u.STATE, state)]
    print("files changed" if any(ch) else "nothing new")

if __name__ == "__main__": main()
