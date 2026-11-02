#!/usr/bin/env python3
"""apply-pipeline — fast, review-gated internship application prep.

The slow way: scrape each posting in a browser, hand-assemble review cards.
This tool does everything UPSTREAM of the browser in one API call:

  inspect <url>   portal detection + posting metadata (title, locations,
                  pay + provenance, deadline, requirements, custom questions,
                  eligibility check against profile.yaml)
  plan <url>      deterministic field-by-field fill plan from profile.yaml
                  (what the browser driver should type, in order)
  card <url>      ready-to-post Kanban review-card body (pay/deadline/answers
                  gate content included — passes review gates on first try)
  check <url>     eligibility only (exit 0 = fits, 1 = suspect, 2 = ineligible)

Portal APIs used (no browser): Greenhouse boards-api, Lever postings API,
Ashby posting API. Workday/others fall back to raw HTML heuristics.

The browser is touched ONLY for the final fill+submit, after human approval.
Profile data lives in profile.yaml (gitignored; see profile.example.yaml).
"""
import argparse, datetime, json, re, sys, urllib.request

ROOT = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
TODAY = datetime.date.today()

def load_profile():
    import os
    p = os.path.join(ROOT, "profile.yaml")
    if not os.path.exists(p):
        return {}
    try:
        import yaml
        return yaml.safe_load(open(p))
    except ImportError:
        # minimal fallback parser for the flat two-level structure
        data, cur = {}, None
        for line in open(p):
            line = line.split("#")[0].rstrip()
            if not line.strip():
                continue
            if not line.startswith(" "):
                cur = line.rstrip(":")
                data[cur] = {}
            else:
                k, _, v = line.strip().partition(":")
                v = v.strip().strip('"')
                if v.lower() in ("null", ""): v = None
                elif v.lower() == "false": v = False
                elif v.lower() == "true": v = True
                data[cur][k] = v
        return data

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (apply-pipeline)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

# ---------- portal detection + metadata ----------

def detect(url):
    if re.search(r"(job-boards\.greenhouse\.io|boards\.greenhouse\.io)/([^/]+)/jobs/(\d+)", url):
        m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
        return "greenhouse", {"board": m.group(1), "job_id": m.group(2)}
    if re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url):
        m = re.search(r"lever\.co/([^/]+)/([0-9a-f-]{36})", url)
        return "lever", {"company": m.group(1), "id": m.group(2)}
    if "jobs.ashbyhq.com" in url:
        m = re.search(r"ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
        if m: return "ashby", {"org": m.group(1), "id": m.group(2)}
    if "myworkdayjobs.com" in url:
        return "workday", {"url": url}
    return "generic", {"url": url}

PAY_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?\s?([\d,]+(?:\.\d+)?)\s*"
    r"(k|K)?(?!\d)")

def extract_pay(content):
    """Return (pay_string, provenance) from posting content. Employer-posted
    only — this reads the req's own pay-transparency section."""
    import html as _html
    text = re.sub(r"<[^>]+>", " ", _html.unescape(_html.unescape(content)))
    for m in PAY_RE.finditer(text):
        lo, hi, k = m.groups()
        ctx = text[max(0, m.start()-120):m.end()+120].lower()
        if any(w in ctx for w in ("salary", "pay", "hour", "annual", "compensation", "base")):
            unit = "k" if k else ""
            return f"${lo}{unit} - ${hi}{unit}", "employer-posted (pay transparency in req)"
    hr = re.search(r"\$\s?(\d{2,3})\s*(?:-|–)\s*\$?\s?(\d{2,3})\s*(?:/\s*hr|per hour)", text, re.I)
    if hr:
        return f"${hr.group(1)}-$ {hr.group(2)}/hr".replace("$ ", "$"), "employer-posted (pay transparency in req)"
    return None, None

DEADLINE_RE = re.compile(r"(?:apply|submission|deadline|closing)(?:\s+\w+){0,4}\s+(?:by|before|on)[:]?\s+([A-Z][a-z]+ \d{1,2},? 20\d\d|\d{1,2}/\d{1,2}/20\d\d)")

def extract_deadline(content):
    import html as _html
    text = re.sub(r"<[^>]+>", " ", _html.unescape(_html.unescape(content)))
    m = DEADLINE_RE.search(text)
    if not m: return None
    for fmt in ("%B %d, %Y", "%B %d %Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(m.group(1).replace(",", ""), fmt).date().isoformat()
        except ValueError:
            pass
    return None

def metadata(url):
    portal, p = detect(url)
    meta = {"portal": portal, "url": url}
    if portal == "greenhouse":
        d = json.loads(fetch(f"https://boards-api.greenhouse.io/v1/boards/{p['board']}/jobs/{p['job_id']}?questions=true"))
        meta.update(title=d.get("title"),
                    locations=[l["name"] for l in d.get("locations", {}).get("all", [])] or
                              ([d["location"]["name"]] if d.get("location") else []),
                    content=d.get("content", ""),
                    questions=[{"label": q.get("label"), "type": q.get("type"),
                                "required": q.get("required"), "value": q.get("value")}
                               for q in d.get("questions", []) if q.get("type") != "phone"],
                    absolute_url=d.get("absolute_url"))
    elif portal == "lever":
        d = json.loads(fetch(f"https://api.lever.co/v0/postings/{p['company']}/{p['id']}"))
        meta.update(title=d.get("text"), locations=[l.get("text") for l in d.get("categories", {}).items() and [d.get("categories", {}).get("location", {})]] if d.get("categories") else [],
                    content=d.get("description", ""))
    elif portal == "ashby":
        d = json.loads(fetch(f"https://api.ashbyhq.com/posting-api/job-board/{p['org']}?includeCompensation=true"))
        job = next((j for j in d.get("jobs", []) if j.get("id") == p["id"]), None)
        if job:
            comp = job.get("compensation") or {}
            meta.update(title=job.get("title"),
                        locations=[job.get("location")],
                        content=job.get("descriptionHtml", ""),
                        pay=(comp.get("compensationTierSummary") and
                             (comp["compensationTierSummary"], "employer-posted (Ashby compensation summary")) or (None, None))
    else:
        html = fetch(url)
        t = re.search(r"<title>([^<]+)", html)
        meta.update(title=t.group(1).strip() if t else None, content=re.sub(r"<[^>]+>", " ", html)[:20000])
    pay, prov = meta.get("pay", (None, None)) if meta.get("pay") else extract_pay(meta.get("content", ""))
    meta.setdefault("pay", pay); meta.setdefault("pay_source", prov)
    meta["deadline"] = extract_deadline(meta.get("content", ""))
    return meta

# ---------- eligibility ----------

def eligibility(meta, profile):
    reasons, suspect = [], []
    import html as _html
    text = re.sub(r"<[^>]+>", " ", _html.unescape(_html.unescape(meta.get("content", "")))).lower()
    grad = (profile.get("logistics", {}) or {}).get("graduation", "2028-05")
    if re.search(r"(master'?s?|ph\.?d)[^.]{0,60}(required|working toward|pursuing)", text):
        suspect.append("appears MS/PhD-targeted")
    if re.search(r"(bachelor|undergrad|bs/ms)", text):
        reasons.append("BS/undergrad eligible")
    if "fall 2027 or spring 2028" in text or "2028" in text:
        reasons.append(f"grad window mentions match {grad}")
    locs = " ".join(meta.get("locations") or []).lower()
    if any(c in locs for c in ("qatar", "london", "uk", "india", "singapore", "australia")) and "united states" not in locs and not re.search(r"\b(us|usa|u\.s\.)\b", locs):
        suspect.append(f"non-US location: {locs}")
    return {"fits": not suspect, "reasons": reasons, "suspect": suspect}

# ---------- fill plan ----------

GREENHOUSE_MAP = {
    "first_name": lambda pr: pr["identity"]["preferred_name"],
    "last_name": lambda pr: pr["identity"]["legal_name"].split()[-1],
    "preferred_name": lambda pr: pr["identity"]["preferred_name"],
    "email": lambda pr: pr["identity"]["email"],
    "phone": lambda pr: pr["identity"]["phone"],
    "candidate-location": lambda pr: pr["identity"]["city_state"],
    "school--0": lambda pr: pr["logistics"]["school"],
    "degree--0": lambda pr: "Bachelor's Degree",
    "discipline--0": lambda pr: "Computer Science",
    "start-year--0": lambda pr: str(pr["logistics"].get("school_start_year", "")),
    "end-year--0": lambda pr: pr["logistics"]["graduation"][:4],
}

def plan(meta, profile):
    steps = []
    ident, log = profile.get("identity", {}), profile.get("logistics", {})
    consent = profile.get("consent", {})
    if meta["portal"] == "greenhouse":
        for q in meta.get("questions", []):
            lbl = (q.get("label") or "").lower()
            if "linkedin" in lbl: steps.append(("linkedin", ident.get("linkedin")))
            elif "website" in lbl or "portfolio" in lbl: steps.append(("website", ident.get("github")))
            elif "availability" in lbl or "confirm" in lbl: steps.append((q["label"], "Yes"))
            elif any(w in lbl for w in ("gender", "hispanic", "veteran", "race", "ethnicity", "disability", "veteran status")):
                steps.append((q["label"], "<BLANK — EEO per profile defaults>"))
    steps.append(("resume", ident.get("resume_pdf")))
    steps.append(("cover_letter", "<none unless posting requires>"))
    never = consent.get("never_automate", [])
    return {"portal": meta["portal"], "steps": steps, "never_automate": never}

# ---------- review card ----------

def card(meta, profile):
    el = eligibility(meta, profile)
    lines = [
        f"APPLY: {meta.get('title')} [{meta['portal']}]",
        f"Posting: {meta['url']}",
        f"Locations: {', '.join(meta.get('locations') or ['?'])}",
        f"Pay: {meta.get('pay') or 'none published'}" + (f" ({meta['pay_source']})" if meta.get("pay_source") else ""),
        f"Deadline: {meta.get('deadline') or 'none stated'}",
        f"Eligibility: {'FITS' if el['fits'] else 'SUSPECT'} — {'; '.join(el['reasons'] + el['suspect']) or '-'}",
        "",
        "Fill plan:",
    ]
    pl = plan(meta, profile)
    for k, v in pl["steps"]:
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("NOT submitted. Awaiting human approval per pipeline gate.")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(prog="apply")
    ap.add_argument("cmd", choices=["inspect", "plan", "card", "check"])
    ap.add_argument("url")
    a = ap.parse_args()
    meta = metadata(a.url)
    prof = load_profile()
    if a.cmd == "inspect":
        out = {k: v for k, v in meta.items() if k != "content"}
        out["content_len"] = len(meta.get("content", ""))
        print(json.dumps(out, indent=2))
    elif a.cmd == "plan":
        print(json.dumps(plan(meta, prof), indent=2))
    elif a.cmd == "card":
        print(card(meta, prof))
    elif a.cmd == "check":
        el = eligibility(meta, prof)
        print(json.dumps(el, indent=2))
        sys.exit(0 if el["fits"] else (2 if any("non-US" in s for s in el["suspect"]) else 1))

if __name__ == "__main__":
    main()
