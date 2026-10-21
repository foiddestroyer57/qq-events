"""Creates one Google Form per event.
If FORM_HELPER_URL is set, the form is made by your Apps Script helper (QQ Form Maker), so it belongs to YOUR Google account.
Otherwise it falls back to the old service-account method."""
import os, json, urllib.request
DEFAULT_QUESTIONS = [
    {"title": "Student name", "type": "short", "required": True},
    {"title": "Class and section", "type": "short", "required": True},
    {"title": "School name", "type": "short", "required": True},
    {"title": "Student / parent mobile number", "type": "short", "required": True},
    {"title": "Email address", "type": "short", "required": True},
    {"title": "Anything else we should know?", "type": "paragraph", "required": False},
    {"title": "Consent", "type": "checkbox", "required": True, "options": ["I confirm the details are correct and a parent/guardian agrees to this registration."]},
]

SCOPES = ["https://www.googleapis.com/auth/forms.body", "https://www.googleapis.com/auth/drive.file"]

def _creds():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES)

def _helper_url():
    return os.environ.get("FORM_HELPER_URL", "").strip()

def services():
    if _helper_url(): return None, None                      # the helper does the work, no Google robot needed
    from googleapiclient.discovery import build
    c = _creds()
    forms = build("forms", "v1", credentials=c, discoveryServiceUrl="https://forms.googleapis.com/$discovery/rest?version=v1", static_discovery=False)
    return forms, build("drive", "v3", credentials=c)

def _item(q, i):
    t, req = q.get("type", "short"), bool(q.get("required"))
    if t in ("dropdown", "checkbox", "radio"):
        opts = [{"value": str(o)[:200]} for o in (q.get("options") or ["Yes"])]
        body = {"choiceQuestion": {"type": {"dropdown": "DROP_DOWN", "checkbox": "CHECKBOX", "radio": "RADIO"}[t], "options": opts}}
    else:
        body = {"textQuestion": {"paragraph": t == "paragraph"}}
    return {"createItem": {"item": {"title": str(q["title"])[:300], "questionItem": {"question": dict(body, required=req)}}, "location": {"index": i}}}

def _helper_create(ev, questions):
    payload = {"key": os.environ.get("FORM_HELPER_KEY", ""), "questions": questions,
               "event": {k: ev.get(k, "") for k in ("name", "date", "venue", "brochure")}}
    req = urllib.request.Request(_helper_url(), data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r: raw = r.read().decode("utf-8", "replace")
    try: out = json.loads(raw)
    except ValueError: raise RuntimeError("the helper did not return JSON - in Deploy > Manage deployments set 'Who has access' to Anyone. Got: " + raw[:80].replace("\n", " "))
    if not out.get("link") or not out.get("id"): raise RuntimeError("helper said: " + str(out.get("error", out))[:150])
    return out["link"], out["id"]

def create_form(forms, drive, ev, questions, admin_email=""):
    if _helper_url(): return _helper_create(ev, questions)
    form = forms.forms().create(body={"info": {"title": ("Registration - " + ev["name"])[:120], "documentTitle": ("QQ Registration - " + ev["name"])[:100]}}).execute()
    desc = "\n".join(x for x in [ev["name"], ("Date: " + ev["date"]) if ev.get("date") else "Date will be announced soon", ("Venue: " + ev["venue"]) if ev.get("venue") else "",
                                  ("Brochure: " + ev["brochure"]) if ev.get("brochure") else ""] if x)
    reqs = [{"updateFormInfo": {"info": {"description": desc}, "updateMask": "description"}}] + [_item(q, i) for i, q in enumerate(questions)]
    forms.forms().batchUpdate(formId=form["formId"], body={"requests": reqs}).execute()
    for em in [x.strip() for x in admin_email.split(",") if x.strip()]:
        try: drive.permissions().create(fileId=form["formId"], body={"type": "user", "role": "writer", "emailAddress": em}).execute()
        except Exception as e: print("  could not share the form with %s: %s" % (em, str(e)[:120]))
    return form["responderUri"], form["formId"]
