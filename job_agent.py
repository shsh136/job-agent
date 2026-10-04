"""Job search agent.

Finds openings that match your profile, analyses each one (AI if ANTHROPIC_API_KEY is
set, otherwise a built-in library), and emails you a digest with job details,
interview prep, and one-tap buttons to let the agent apply or skip.

Usage:
    python job_agent.py                     # search, analyse, email the digest (scheduled daily)
    python job_agent.py --dry-run           # same, but write digest.html and send nothing
    python job_agent.py check-replies       # act on APPLY / SKIP / APPLIED emails you sent (scheduled hourly)
    python job_agent.py apply J7F3A         # prepare an application; emails you the kit
    python job_agent.py apply J7F3A --send  # ...and email the employer directly if the posting has an apply address
    python job_agent.py mark J7F3A interview  # applied | skipped | interview | rejected | offer
    python job_agent.py status              # list your applications
    python job_agent.py prep "SRE engineer" # email yourself interview prep for any role
"""

import argparse
import base64
import email
import email.header
import email.utils
import hashlib
import html
import imaplib
import json
import mimetypes
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import ai
import gemini
import prep_library

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
PROFILE_PATH = BASE_DIR / "profile.json"
ENV_PATH = BASE_DIR / ".env"
SEEN_PATH = BASE_DIR / "seen_jobs.json"
JOBS_PATH = BASE_DIR / "jobs.json"
APPS_PATH = BASE_DIR / "applications.json"
APPS_DIR = BASE_DIR / "applications"
DIGEST_PATH = BASE_DIR / "digest.html"
LOG_PATH = BASE_DIR / "agent.log"

USER_AGENT = "Mozilla/5.0 (job-agent; personal use)"
AGENT_HEADER = "X-Job-Agent"  # set on every email the agent sends, so check-replies ignores them
REQUIRED_PROFILE_FIELDS = ["current_city", "notice_period", "current_ctc_lpa", "expected_ctc_lpa"]
STATUSES = ["kit_sent", "applied", "skipped", "interview", "rejected", "offer"]


# ---------------------------------------------------------------- utilities

def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_env(path):
    env = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def short_id(key):
    """Stable 5-character id like 'J7F3A' that you can type in a reply."""
    return base64.b32encode(hashlib.sha1(key.encode()).digest()).decode()[:5]


def esc(value):
    return html.escape(str(value or ""))


def make_job(source, job_id, title, company, location, description, url, posted,
             remote=False, salary="", job_type=""):
    key = f"{source}:{job_id}"
    return {
        "id": short_id(key),
        "key": key,
        "source": source,
        "title": (title or "").strip(),
        "company": (company or "Unknown").strip(),
        "location": (location or "").strip(),
        "description": strip_html(description),
        "url": url or "",
        "posted": (posted or "")[:10],
        "remote": remote,
        "salary": salary or "",
        "job_type": job_type or "",
    }


def salary_range(lo, hi, currency="", period=""):
    def fmt(n):
        return f"{n / 100000:.1f}L" if currency in ("INR", "₹") and n >= 100000 else f"{n:,.0f}"
    if not lo and not hi:
        return ""
    text = fmt(lo) if lo == hi or not hi else f"{fmt(lo or hi)}–{fmt(hi)}" if lo else f"up to {fmt(hi)}"
    return f"{currency} {text}{' / ' + period if period else ''}".strip()


# ------------------------------------------------------------------ sources

def fetch_adzuna(cfg, env):
    app_id, app_key = env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log("Adzuna: skipped (ADZUNA_APP_ID / ADZUNA_APP_KEY not set in .env)")
        return []
    jobs = []
    for query in cfg["search_queries"]:
        params = urllib.parse.urlencode({
            "app_id": app_id,
            "app_key": app_key,
            "what": query,
            "results_per_page": 50,
            "max_days_old": cfg["max_days_old"],
            "content-type": "application/json",
        })
        data = http_get_json(f"https://api.adzuna.com/v1/api/jobs/in/search/1?{params}")
        for r in data.get("results", []):
            jobs.append(make_job(
                "Adzuna", r.get("id"), r.get("title"),
                (r.get("company") or {}).get("display_name"),
                (r.get("location") or {}).get("display_name"),
                r.get("description"), r.get("redirect_url"), r.get("created"),
                salary=salary_range(r.get("salary_min"), r.get("salary_max"), "INR", "year"),
                job_type=" ".join(x for x in (r.get("contract_time"), r.get("contract_type")) if x),
            ))
    return jobs


def fetch_jsearch(cfg, env):
    key = env.get("RAPIDAPI_KEY")
    if not key:
        log("JSearch: skipped (RAPIDAPI_KEY not set in .env)")
        return []
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    date_posted = "3days" if cfg["max_days_old"] <= 3 else "week"
    jobs = []
    # Free tier has a small monthly quota, so only the first few queries are used.
    for query in cfg["search_queries"][:3]:
        params = urllib.parse.urlencode({
            "query": f"{query} in India",
            "page": 1,
            "num_pages": 1,
            "country": "in",
            "date_posted": date_posted,
        })
        data = http_get_json(f"https://jsearch.p.rapidapi.com/search-v2?{params}", headers)
        payload = data.get("data") or {}
        results = payload.get("jobs", []) if isinstance(payload, dict) else payload
        for r in results:
            loc = ", ".join(x for x in (r.get("job_city"), r.get("job_country")) if x) or r.get("job_location")
            salary = r.get("job_salary_string") or salary_range(
                r.get("job_min_salary"), r.get("job_max_salary"),
                r.get("job_salary_currency") or "", (r.get("job_salary_period") or "").lower())
            job = make_job(
                "JSearch", r.get("job_uid") or r.get("job_id"), r.get("job_title"), r.get("employer_name"),
                loc, r.get("job_description"), r.get("job_apply_link"),
                r.get("job_posted_at_datetime_utc"), bool(r.get("job_is_remote")),
                salary=salary, job_type=(r.get("job_employment_type") or "").replace("‑", "-"),
            )
            if r.get("job_publisher"):
                job["source"] = f"{r['job_publisher']} (JSearch)"
            jobs.append(job)
    return jobs


def remote_location_ok(location, cfg):
    loc = (location or "").lower()
    return not loc or any(word in loc for word in cfg["remote_locations_allowed"])


def fetch_remotive(cfg, env):
    jobs = []
    for term in ("python", "devops", "backend"):
        data = http_get_json(f"https://remotive.com/api/remote-jobs?search={term}&limit=100")
        for r in data.get("jobs", []):
            if not remote_location_ok(r.get("candidate_required_location"), cfg):
                continue
            jobs.append(make_job(
                "Remotive", r.get("id"), r.get("title"), r.get("company_name"),
                f"Remote ({r.get('candidate_required_location') or 'Anywhere'})",
                r.get("description"), r.get("url"), r.get("publication_date"), True,
                salary=r.get("salary") or "", job_type=(r.get("job_type") or "").replace("_", " "),
            ))
    return jobs


def fetch_arbeitnow(cfg, env):
    data = http_get_json("https://www.arbeitnow.com/api/job-board-api")
    jobs = []
    for r in data.get("data", []):
        if not r.get("remote"):
            continue
        created = r.get("created_at")
        posted = datetime.fromtimestamp(created, timezone.utc).isoformat() if created else ""
        jobs.append(make_job(
            "Arbeitnow", r.get("slug"), r.get("title"), r.get("company_name"),
            f"Remote ({r.get('location') or 'Anywhere'})",
            r.get("description"), r.get("url"), posted, True,
            job_type=", ".join(r.get("job_types") or []),
        ))
    return jobs


SOURCES = {
    "adzuna": fetch_adzuna,
    "jsearch": fetch_jsearch,
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
}


# ------------------------------------------------------------------ scoring

def contains(text, term):
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def required_years(text):
    """Smallest 'N years' requirement mentioned in the description, or None."""
    found = re.findall(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2})?\s*\+?\s*(?:years|yrs)", text)
    years = [int(n) for n in found if 0 < int(n) < 30]
    return min(years) if years else None


def score_job(job, cfg):
    title = job["title"].lower()
    text = f"{title} {job['description'].lower()}"
    reasons = []

    if any(contains(title, k) for k in cfg["exclude_title_keywords"]):
        return 0, ["excluded by title keyword"]

    skills = cfg["skills"]
    matched = [s for s in skills if contains(text, s)]
    skill_points = sum(skills[s] for s in matched)
    # 45 weighted points (e.g. Python + Azure + Flask + REST + Vault + ...) counts as a full skill match.
    skill_score = min(60, round(skill_points / 45 * 60))
    if matched:
        top = sorted(matched, key=lambda s: -skills[s])[:8]
        reasons.append("skills: " + ", ".join(top))

    title_score = 25 if any(contains(title, k) for k in cfg["title_keywords"]) else 0
    if title_score:
        reasons.append("title fits")

    years = required_years(text)
    mine = cfg["experience_years"]
    if years is None:
        exp_score = 10
    elif years <= mine + 1:
        exp_score = 15
        reasons.append(f"asks {years}+ yrs")
    elif years <= mine + 2:
        exp_score = 5
        reasons.append(f"asks {years}+ yrs (stretch)")
    else:
        exp_score = -20
        reasons.append(f"asks {years}+ yrs (too senior)")

    senior_penalty = -15 if any(contains(title, k) for k in cfg["senior_title_keywords"]) else 0
    if senior_penalty:
        reasons.append("senior title")

    score = max(0, min(100, skill_score + title_score + exp_score + senior_penalty))
    return score, reasons


def dedupe(jobs):
    """Drop duplicates by source id and by title+company across sources."""
    out, keys = [], set()
    for job in jobs:
        norm = re.sub(r"[^a-z0-9]", "", (job["title"] + job["company"]).lower())
        if job["key"] in keys or norm in keys:
            continue
        keys.update((job["key"], norm))
        out.append(job)
    return out


def save_seen(seen):
    # Forget jobs older than 60 days so the file doesn't grow forever.
    cutoff = (datetime.now() - timedelta(days=60)).isoformat()
    save_json(SEEN_PATH, {k: v for k, v in seen.items() if v >= cutoff})


# ------------------------------------------------------- profile & analysis

def load_profile():
    return {k: v for k, v in load_json(PROFILE_PATH, {}).items() if not k.startswith("_")}


def missing_profile_fields(profile):
    return [f for f in REQUIRED_PROFILE_FIELDS if not profile.get(f) or str(profile[f]).startswith("TODO")]


def resume_text(cfg, profile):
    """Resume as text for the AI: the portfolio YAML if present, else the PDF."""
    yml = (BASE_DIR / cfg.get("resume_yaml", "")).resolve()
    if cfg.get("resume_yaml") and yml.exists():
        return yml.read_text(encoding="utf-8")
    pdf = (BASE_DIR / profile.get("resume_pdf", "")).resolve()
    if pdf.exists():
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
    return ""


def profile_text(profile):
    return "\n".join(f"{k}: {v}" for k, v in profile.items()
                     if v and not str(v).startswith("TODO") and k != "resume_pdf")


def llm(cfg, env):
    """The AI module to use (Claude if its key is set, else Gemini), or None."""
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("enabled", True):
        return None
    if ai.available(env):
        return ai
    if gemini.available(env):
        gemini.PREFERRED_MODEL = ai_cfg.get("gemini_model", "")
        return gemini
    return None


def analyse(jobs, cfg, env, profile):
    """Attach an 'insight' (summary, gaps, interview prep) to each job."""
    ai_cfg = cfg.get("ai", {})
    provider = llm(cfg, env)
    use_ai = provider is not None
    if not use_ai:
        log("AI analysis off (set GEMINI_API_KEY in .env to enable); using built-in prep library")
    resume, ptext = (resume_text(cfg, profile), profile_text(profile)) if use_ai else ("", "")
    failures = 0
    for i, job in enumerate(jobs):
        if use_ai and i < ai_cfg.get("max_jobs_per_run", 10) and failures < 2:
            try:
                job["insight"] = provider.analyze_job(env, job, resume, ptext, ai_cfg.get("effort", "medium"))
                failures = 0
                continue
            except Exception as e:  # never let one bad AI call kill the digest
                failures += 1
                log(f"AI analysis failed for {job['id']}: {str(e)[:200]}")
                if failures == 2:
                    log("AI failed twice in a row; using built-in prep for the rest of this run")
        job["insight"] = prep_library.offline_insight(job, list(cfg["skills"]))


# -------------------------------------------------------------------- email

def send_email(env, to, subject, body_html, cc=None, reply_to=None, attachments=(), kind="digest"):
    user, password = env.get("SMTP_USER"), env.get("SMTP_APP_PASSWORD")
    if not (user and password):
        raise RuntimeError("SMTP_USER / SMTP_APP_PASSWORD not set in .env")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    msg[AGENT_HEADER] = kind
    msg.set_content(strip_html(body_html))
    msg.add_alternative(body_html, subtype="html")
    for path in attachments:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)


def mailto(env, command, job_id):
    addr = env.get("SMTP_USER") or ""
    return f"mailto:{addr}?subject={urllib.parse.quote(f'{command} {job_id}')}&body={urllib.parse.quote(f'{command} {job_id}')}"


def button(href, label, color):
    return (f'<a href="{esc(href)}" style="display:inline-block;padding:7px 14px;margin:4px 6px 0 0;'
            f'background:{color};color:#fff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600">{label}</a>')


def chips(items, color):
    return " ".join(f'<span style="display:inline-block;padding:2px 8px;margin:2px;border-radius:10px;'
                    f'background:{color};font-size:12px">{esc(i)}</span>' for i in items)


def job_card(job, env):
    ins = job.get("insight", {})
    score = job["score"]
    color = "#1a7f37" if score >= 75 else "#9a6700" if score >= 60 else "#57606a"
    facts = [f for f in (job["company"], job["location"], job.get("job_type"), job.get("salary") or "salary not listed",
                         f"posted {job['posted']}" if job.get("posted") else "", f"via {job['source']}") if f]

    questions = "".join(
        f'<li style="margin-bottom:6px">{esc(q["question"])}'
        + (f'<br><span style="color:#57606a;font-size:12px">Tip: {esc(q["hint"])}</span>' if q.get("hint") else "")
        + "</li>" for q in ins.get("interview_questions", []))
    resources = " &middot; ".join(f'<a href="{esc(r["url"])}" style="color:#0645ad">{esc(r["title"])}</a>'
                                  for r in ins.get("resources", []))

    return f"""
<table role="presentation" width="100%" style="border:1px solid #d0d7de;border-radius:8px;margin:0 0 18px;border-collapse:separate">
<tr><td style="padding:16px">
  <div style="font-size:12px;color:#57606a">ID <b style="color:#1f2328;font-family:Consolas,monospace">{job['id']}</b>
    &nbsp;&middot;&nbsp; match <b style="color:{color}">{score}/100</b></div>
  <a href="{esc(job['url'])}" style="font-size:17px;font-weight:700;color:#0645ad;text-decoration:none">{esc(job['title'])}</a>
  <div style="color:#1f2328;margin:4px 0 10px">{' &middot; '.join(esc(f) for f in facts)}</div>

  {f'<div style="margin-bottom:8px"><b>{esc(ins["fit_verdict"])}</b></div>' if ins.get("fit_verdict") else ''}
  <div style="margin-bottom:8px">{esc(ins.get('summary'))}</div>
  {f'<div style="margin-bottom:6px"><b>Requirements:</b> {esc("; ".join(ins["key_requirements"]))}</div>' if ins.get("key_requirements") else ''}
  {f'<div style="margin-bottom:4px"><b>You have:</b> {chips(ins["matched_skills"], "#dafbe1")}</div>' if ins.get("matched_skills") else ''}
  {f'<div style="margin-bottom:8px"><b>Gaps:</b> {chips(ins["missing_skills"], "#ffebe9")}</div>' if ins.get("missing_skills") else ''}
  {f'<div style="margin-bottom:8px;padding:8px;background:#f6f8fa;border-radius:6px"><b>How to pitch yourself:</b> {esc(ins["how_to_pitch"])}</div>' if ins.get("how_to_pitch") else ''}

  <details open><summary style="cursor:pointer;font-weight:700;margin:8px 0">Interview prep <span style="font-weight:400;color:#57606a;font-size:12px">({esc(ins.get('source', ''))})</span></summary>
    <ol style="padding-left:20px;margin:6px 0">{questions}</ol>
    {f'<div style="margin:6px 0"><b>Revise:</b> {esc("; ".join(ins.get("topics_to_revise", [])))}</div>' if ins.get("topics_to_revise") else ''}
    {f'<div style="margin:6px 0"><b>Resources:</b> {resources}</div>' if resources else ''}
  </details>

  <div style="margin-top:10px">
    {button(job['url'], 'Open job posting', '#0969da')}
    {button(mailto(env, 'APPLY', job['id']), 'Let agent apply', '#1a7f37')}
    {button(mailto(env, 'APPLIED', job['id']), 'I applied myself', '#57606a')}
    {button(mailto(env, 'SKIP', job['id']), 'Skip', '#8c959f')}
  </div>
</td></tr></table>"""


def build_digest(new_jobs, reminders, apps, env):
    counts = {s: sum(1 for a in apps.values() if a["status"] == s) for s in STATUSES}
    tracker = " &middot; ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items() if n) or "no applications yet"
    reminder_rows = "".join(
        f'<li><a href="{esc(j["url"])}" style="color:#0645ad">{esc(j["title"])}</a>, {esc(j["company"])} '
        f'<span style="color:#57606a">({j["score"]}/100, found {j["found_at"][:10]}, ID <b>{j["id"]}</b>)</span> '
        f'<a href="{esc(mailto(env, "APPLY", j["id"]))}">apply</a> &middot; <a href="{esc(mailto(env, "SKIP", j["id"]))}">skip</a></li>'
        for j in reminders)
    return f"""<html><body style="margin:0;padding:16px;font-family:Segoe UI,Arial,sans-serif;color:#1f2328;font-size:14px;line-height:1.45">
<div style="max-width:760px;margin:0 auto">
  <h2 style="margin:0 0 4px">{len(new_jobs)} new job match{'es' if len(new_jobs) != 1 else ''}</h2>
  <p style="color:#57606a;margin:0 0 6px">{datetime.now():%A, %d %b %Y} &middot; Your applications: {tracker}</p>
  <p style="color:#57606a;margin:0 0 18px;font-size:12px">Tap <b>Let agent apply</b> to send a pre-filled reply.
    The agent then prepares a tailored cover letter and form answers, and emails the employer directly if the posting lists an apply address.</p>
  {''.join(job_card(j, env) for j in new_jobs)}
  {f'<h3 style="margin:24px 0 6px">Still waiting for your decision</h3><ul style="padding-left:20px">{reminder_rows}</ul>' if reminders else ''}
</div></body></html>"""


# ---------------------------------------------------------------- commands

def cmd_run(args, cfg, env):
    profile = load_profile()
    jobs = []
    for name, fetch in SOURCES.items():
        if not cfg["sources"].get(name):
            continue
        try:
            found = fetch(cfg, env)
            log(f"{name}: {len(found)} jobs fetched")
            jobs.extend(found)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            log(f"{name}: FAILED - {e}")

    jobs = dedupe(jobs)
    seen = load_json(SEEN_PATH, {})
    fresh = [j for j in jobs if j["key"] not in seen]

    matches = []
    for job in fresh:
        job["score"], job["reasons"] = score_job(job, cfg)
        if job["score"] >= cfg["min_score"]:
            matches.append(job)
    matches.sort(key=lambda j: -j["score"])
    matches = matches[: cfg["max_jobs_in_email"]]
    log(f"{len(jobs)} unique jobs, {len(fresh)} not seen before, {len(matches)} scored >= {cfg['min_score']}")

    analyse(matches, cfg, env, profile)

    # Earlier strong matches you haven't acted on yet.
    stored, apps = load_json(JOBS_PATH, {}), load_json(APPS_PATH, {})
    remind_after = (datetime.now() - timedelta(days=cfg.get("remind_days", 5))).isoformat()
    reminders = sorted(
        (j for jid, j in stored.items()
         if jid not in apps and j["score"] >= cfg.get("remind_min_score", 65) and j["found_at"] >= remind_after),
        key=lambda j: -j["score"])[:5]

    if matches or reminders:
        digest = build_digest(matches, reminders, apps, env)
        DIGEST_PATH.write_text(digest, encoding="utf-8")
        if args.dry_run:
            log(f"Dry run: digest written to {DIGEST_PATH}")
        else:
            subject = f"{len(matches)} new job matches - {datetime.now():%d %b}"
            send_email(env, cfg["email_to"], subject, digest)
            log(f"Email sent to {cfg['email_to']}")
    else:
        log("No new matches and nothing pending; no email sent.")

    if not args.dry_run:
        now = datetime.now().isoformat()
        for job in fresh:
            seen[job["key"]] = now
        save_seen(seen)
        for job in matches:
            job["found_at"] = now
            stored[job["id"]] = job
        cutoff = (datetime.now() - timedelta(days=45)).isoformat()
        save_json(JOBS_PATH, {k: v for k, v in stored.items() if v["found_at"] >= cutoff or k in apps})
    return 0


def find_apply_emails(text):
    found = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+\w", text)
    bad = ("noreply", "no-reply", "donotreply", "example.", "privacy", "unsubscribe")
    return list(dict.fromkeys(e for e in found if not any(b in e.lower() for b in bad)))


def template_cover_letter(job, profile):
    return (f"Dear Hiring Team,\n\nI'm applying for the {job['title']} role at {job['company']}. "
            f"I'm a Python backend and cloud automation engineer with {profile.get('total_experience_years', '2')} years at "
            f"{profile.get('current_company', '')}, where I build Flask microservices that automate VM provisioning across Azure "
            "and AWS (5,000+ requests a month), and I led the move from static credentials to HashiCorp Vault ephemeral keys "
            "and OAuth 2.0 across 24 services.\n\n"
            f"My resume is attached. My notice period is {profile.get('notice_period', '')}. I'd welcome the chance to discuss "
            f"how I can contribute.\n\nThank you,\n{profile.get('full_name')}\n{profile.get('phone')}\n{profile.get('linkedin')}")


def form_answers(job, profile, insight):
    rows = [
        ("Full name", profile.get("full_name")), ("Email", profile.get("email")), ("Phone", profile.get("phone")),
        ("Current location", profile.get("current_city")), ("Preferred locations", ", ".join(profile.get("preferred_locations", []))),
        ("Willing to relocate", profile.get("willing_to_relocate")),
        ("Total experience", f"{profile.get('total_experience_years')} years"),
        ("Current company / title", f"{profile.get('current_company')} / {profile.get('current_title')}"),
        ("Current CTC", f"{profile.get('current_ctc_lpa')} LPA"), ("Expected CTC", f"{profile.get('expected_ctc_lpa')} LPA"),
        ("Notice period", profile.get("notice_period")), ("Earliest start date", profile.get("earliest_start_date")),
        ("Work authorization", profile.get("work_authorization")), ("Highest degree", profile.get("highest_degree")),
        ("Bachelor's", profile.get("bachelors")), ("LinkedIn", profile.get("linkedin")),
        ("GitHub", profile.get("github")), ("Portfolio", profile.get("portfolio")),
        ("Why this role?", insight.get("how_to_pitch") or f"The {job['title']} role matches my backend and cloud automation work."),
    ]
    return [(q, a) for q, a in rows if a and "None" not in str(a)]


def set_status(job_id, status, method=""):
    apps, jobs = load_json(APPS_PATH, {}), load_json(JOBS_PATH, {})
    job = jobs.get(job_id, {})
    entry = apps.setdefault(job_id, {"title": job.get("title", ""), "company": job.get("company", ""),
                                     "url": job.get("url", ""), "history": []})
    entry["status"] = status
    entry["updated"] = datetime.now().isoformat(timespec="minutes")
    entry["history"].append(f"{entry['updated']} {status}{' via ' + method if method else ''}")
    save_json(APPS_PATH, apps)


def apply_to(job_id, cfg, env, send=False):
    """Prepare (and optionally send) an application. Returns a short result string."""
    job = load_json(JOBS_PATH, {}).get(job_id)
    if not job:
        return f"{job_id}: unknown job id (only jobs from digests are stored)"
    profile = load_profile()
    missing = missing_profile_fields(profile)
    if missing:
        return f"{job_id}: fill these in profile.json first: {', '.join(missing)}"
    resume_pdf = (BASE_DIR / profile.get("resume_pdf", "")).resolve()
    if not resume_pdf.exists():
        return f"{job_id}: resume not found at {resume_pdf}"

    insight = job.get("insight", {})
    letter = ""
    provider = llm(cfg, env)
    if provider:
        try:
            letter = provider.cover_letter(env, job, resume_text(cfg, profile), profile_text(profile))
            if profile.get("phone") and profile["phone"] not in letter:
                letter += f"\n{profile['phone']}"
        except Exception as e:
            log(f"AI cover letter failed for {job_id}: {e}")
    letter = letter or template_cover_letter(job, profile)
    answers = form_answers(job, profile, insight)

    out_dir = APPS_DIR / f"{job_id}_{re.sub(r'[^A-Za-z0-9]+', '_', job['company'])[:30]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cover_letter.txt").write_text(letter, encoding="utf-8")
    (out_dir / "form_answers.txt").write_text("\n".join(f"{q}: {a}" for q, a in answers), encoding="utf-8")
    (out_dir / "job.json").write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")

    employer = find_apply_emails(job["description"])
    letter_html = esc(letter).replace("\n", "<br>")
    if employer and send:
        send_email(env, employer[0], f"Application for {job['title']} - {profile['full_name']}",
                   f"<html><body style='font-family:Segoe UI,Arial,sans-serif'>{letter_html}</body></html>",
                   cc=profile["email"], reply_to=profile["email"], attachments=[resume_pdf], kind="application")
        set_status(job_id, "applied", f"email to {employer[0]}")
        return f"{job_id}: application emailed to {employer[0]} (you're CC'd)"

    # No apply address (most job boards): email you a ready-to-paste kit instead.
    rows = "".join(f"<tr><td style='padding:4px 10px 4px 0;color:#57606a;vertical-align:top'>{esc(q)}</td>"
                   f"<td style='padding:4px 0'>{esc(a)}</td></tr>" for q, a in answers)
    note = (f"This posting lists <b>{esc(employer[0])}</b>. Run <code>python job_agent.py apply {job_id} --send</code> "
            f"or reply <b>APPLY {job_id}</b> to have the agent email it." if employer else
            "This job board needs you to apply on its site (the agent can't log in to LinkedIn, Naukri, Workday, etc. for you). "
            "Everything you need is below and your resume is attached. It takes about 2 minutes.")
    kit = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328;font-size:14px;max-width:720px">
<h2 style="margin-bottom:4px">Application kit: {esc(job['title'])}</h2>
<p style="margin-top:0;color:#57606a">{esc(job['company'])} &middot; {esc(job['location'])} &middot; ID {job_id}</p>
<p>{note}</p>
<p>{button(job['url'], 'Open application page', '#0969da')} {button(mailto(env, 'APPLIED', job_id), 'Mark as applied', '#1a7f37')}</p>
<h3>Cover letter</h3><div style="padding:12px;background:#f6f8fa;border-radius:6px">{letter_html}</div>
<h3>Form answers</h3><table>{rows}</table>
</body></html>"""
    send_email(env, cfg["email_to"], f"Apply now: {job['title']} at {job['company']}", kit,
               attachments=[resume_pdf], kind="kit")
    set_status(job_id, "kit_sent")
    return f"{job_id}: application kit emailed to you" + (f" (employer address found: {employer[0]}; add --send to email it)" if employer and not send else "")


def cmd_apply(args, cfg, env):
    print(apply_to(args.job_id.upper(), cfg, env, send=args.send))
    return 0


def body_text(msg):
    """Plain-text body of a reply, without the quoted original."""
    part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    if part is None:
        return ""
    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:\s*$", line):
            break
        lines.append(line)
    return "\n".join(lines)


COMMAND_RE = re.compile(r"^\s*(?:re:\s*)?(APPLY|APPLIED|SKIP|INTERVIEW|REJECTED|OFFER)\s+([A-Z0-9][A-Z0-9 ,]*)$", re.I | re.M)


def cmd_check_replies(args, cfg, env):
    """Read the sender inbox for command emails from you, e.g. subject 'APPLY J7F3A'."""
    user, password = env.get("SMTP_USER"), env.get("SMTP_APP_PASSWORD")
    if not (user and password):
        log("check-replies: SMTP_USER / SMTP_APP_PASSWORD not set in .env")
        return 1
    since = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, password.replace(" ", ""))
    imap.select("INBOX")
    _, data = imap.search(None, f'(UNSEEN FROM "{cfg["email_to"]}" SINCE {since})')
    handled = 0
    for num in data[0].split():
        # PEEK so digests and other mail stay unread; only command emails get marked read below.
        _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
        msg = email.message_from_bytes(msg_data[0][1])
        if msg.get(AGENT_HEADER):
            continue  # one of our own emails
        sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        if sender != cfg["email_to"].lower():
            continue
        subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
        results = []
        for verb, ids in COMMAND_RE.findall(f"{subject}\n{body_text(msg)}"):
            for job_id in re.findall(r"\b[A-Z0-9]{5}\b", ids.upper()):
                verb = verb.upper()
                if verb == "APPLY":
                    # Replying APPLY is your explicit go-ahead to email the employer if possible.
                    results.append(apply_to(job_id, cfg, env, send=True))
                else:
                    status = {"APPLIED": "applied", "SKIP": "skipped"}.get(verb, verb.lower())
                    set_status(job_id, status, "email reply")
                    results.append(f"{job_id}: marked {status}")
        for r in results:
            log(f"reply command -> {r}")
        if results and not any(r.endswith("application kit emailed to you") for r in results):
            send_email(env, cfg["email_to"], "Job agent: done",
                       "<html><body>" + "<br>".join(esc(r) for r in results) + "</body></html>", kind="ack")
        if results:
            imap.store(num, "+FLAGS", "\\Seen")  # so it isn't processed again
            handled += 1
    imap.logout()
    log(f"check-replies: {handled} command email(s) processed")
    return 0


def cmd_mark(args, cfg, env):
    set_status(args.job_id.upper(), args.status, "manual")
    print(f"{args.job_id.upper()}: marked {args.status}")
    return 0


def cmd_status(args, cfg, env):
    apps = load_json(APPS_PATH, {})
    if not apps:
        print("No applications yet.")
    for jid, a in sorted(apps.items(), key=lambda kv: kv[1]["updated"], reverse=True):
        print(f"{jid}  {a['status']:<9} {a['updated'][:10]}  {a['title'][:45]:<45}  {a['company'][:25]}")
    return 0


def cmd_prep(args, cfg, env):
    job = {"id": "PREP", "key": "prep", "title": args.role, "company": args.company or "a typical company",
           "location": "", "description": args.role, "url": "", "posted": "", "source": "manual",
           "score": 100, "salary": "", "job_type": ""}
    analyse([job], cfg, env, load_profile())
    card = job_card(job, env)
    page = f"<html><body style='font-family:Segoe UI,Arial,sans-serif;max-width:760px'><h2>Interview prep: {esc(args.role)}</h2>{card}</body></html>"
    out = BASE_DIR / "prep.html"
    out.write_text(page, encoding="utf-8")
    if args.email:
        send_email(env, cfg["email_to"], f"Interview prep: {args.role}", page, kind="prep")
        print(f"Prep emailed to {cfg['email_to']}")
    print(f"Prep written to {out}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="run: don't send email or update state")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="search, analyse and email the digest (default)")
    sub.add_parser("check-replies", help="act on APPLY / SKIP / APPLIED emails")
    p = sub.add_parser("apply", help="prepare an application for a job id")
    p.add_argument("job_id")
    p.add_argument("--send", action="store_true", help="email the employer if the posting has an apply address")
    p = sub.add_parser("mark", help="update an application's status")
    p.add_argument("job_id")
    p.add_argument("status", choices=STATUSES)
    sub.add_parser("status", help="list applications")
    p = sub.add_parser("prep", help="interview prep for any role")
    p.add_argument("role")
    p.add_argument("--company", default="")
    p.add_argument("--email", action="store_true")
    args = parser.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    env = load_env(ENV_PATH)
    commands = {None: cmd_run, "run": cmd_run, "check-replies": cmd_check_replies, "apply": cmd_apply,
                "mark": cmd_mark, "status": cmd_status, "prep": cmd_prep}
    return commands[args.command](args, cfg, env)


if __name__ == "__main__":
    sys.exit(main())
