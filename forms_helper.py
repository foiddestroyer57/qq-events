"""Creates one Google Form per event (owned by the service account, shared to ADMIN_EMAIL so you can see the responses)."""
import os
DEFAULT_QUESTIONS = [
    {"title": "Student name", "type": "short", "required": True},
    {"title": "Class and section", "type": "short", "required": True},
    {"title": "School name", "type": "short", "required": True},
    {"title": "Student / parent mobile number", "type": "short", "required": True},
    {"title": "Email address", "type": "short", "required": True},
    {"title": "Teacher in charge (name and mobile number)", "type": "short", "required": False},
    {"title": "Anything else we should know?", "type": "paragraph", "required": False},
    {"title": "Consent", "type": "checkbox", "required": True, "options": ["I confirm the details are correct and a parent/guardian agrees to this registration."]},
]

SCOPES = ["https://www.googleapis.com/auth/forms.body", "https://www.googleapis.com/auth/drive.file"]

def _creds():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES)

def services():
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

def create_form(forms, drive, ev, questions, admin_email=""):
    form = forms.forms().create(body={"info": {"title": ("Registration - " + ev["name"])[:120], "documentTitle": ("QQ Registration - " + ev["name"])[:100]}}).execute()
    desc = "\n".join(x for x in [ev["name"], ("Date: " + ev["date"]) if ev.get("date") else "Date will be announced soon", ("Venue: " + ev["venue"]) if ev.get("venue") else "",
                                  ("Brochure: " + ev["brochure"]) if ev.get("brochure") else ""] if x)
    reqs = [{"updateFormInfo": {"info": {"description": desc}, "updateMask": "description"}}] + [_item(q, i) for i, q in enumerate(questions)]
    forms.forms().batchUpdate(formId=form["formId"], body={"requests": reqs}).execute()
    for em in [x.strip() for x in admin_email.split(",") if x.strip()]:
        try: drive.permissions().create(fileId=form["formId"], body={"type": "user", "role": "writer", "emailAddress": em}).execute()
        except Exception as e: print("  could not share the form with %s: %s" % (em, str(e)[:120]))
    return form["responderUri"], form["formId"]
