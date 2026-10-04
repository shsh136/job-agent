"""Google Gemini (free tier) version of ai.py: same functions, same output shape.

Uses the REST API directly, so nothing extra to install. The model is picked at
runtime from the models your key can use (newest non-preview "flash" model),
unless config.json sets "ai": {"gemini_model": "..."}.
"""

import json
import re
import time
import urllib.error
import urllib.request

from ai import COVER_INSTRUCTIONS, INSIGHT_INSTRUCTIONS

API = "https://generativelanguage.googleapis.com/v1beta"
_model_cache = {}
PREFERRED_MODEL = ""  # set from config.json "ai.gemini_model"; empty = auto-pick

_STR = {"type": "STRING"}
_STR_LIST = {"type": "ARRAY", "items": _STR}
INSIGHT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": _STR,
        "key_requirements": _STR_LIST,
        "matched_skills": _STR_LIST,
        "missing_skills": _STR_LIST,
        "fit_verdict": _STR,
        "how_to_pitch": _STR,
        "interview_questions": {"type": "ARRAY", "items": {
            "type": "OBJECT", "properties": {"question": _STR, "hint": _STR}, "required": ["question", "hint"]}},
        "topics_to_revise": _STR_LIST,
        "resources": {"type": "ARRAY", "items": {
            "type": "OBJECT", "properties": {"title": _STR, "url": _STR}, "required": ["title", "url"]}},
    },
    "required": ["summary", "key_requirements", "matched_skills", "missing_skills", "fit_verdict",
                 "how_to_pitch", "interview_questions", "topics_to_revise", "resources"],
}


def available(env):
    return bool(env.get("GEMINI_API_KEY"))


class Busy(RuntimeError):
    """Model overloaded or rate-limited; try another model."""


def _request(env, path, body=None, tries=2):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}/{path}", data=data, method="POST" if data else "GET",
                                 headers={"x-goog-api-key": env["GEMINI_API_KEY"], "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 503):
                if attempt < tries - 1:
                    time.sleep(15)  # one short retry; after that, switch model
                    continue
                raise Busy(f"Gemini HTTP {e.code}: {detail}") from None
            raise RuntimeError(f"Gemini HTTP {e.code}: {detail}") from None


def candidate_models(env):
    """Usable free models, best first: newest stable 'flash', then older/lite/preview ones."""
    if "list" not in _model_cache:
        models = _request(env, "models?pageSize=1000").get("models", [])
        names = [m["name"].removeprefix("models/") for m in models
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [n for n in names if "flash" in n and not re.search(r"image|tts|audio|live|embed|thinking", n)]
        version = lambda n: float((re.findall(r"\d+(?:\.\d+)?", n) or ["0"])[0])
        rank = lambda n: (bool(re.fullmatch(r"gemini-[\d.]+-flash(-latest)?", n)), "lite" not in n, version(n))
        ordered = sorted(flash, key=rank, reverse=True)
        if not ordered:
            raise RuntimeError("no Gemini flash model available for this key")
        _model_cache["list"] = ordered
    models = list(_model_cache["list"])
    if PREFERRED_MODEL:
        models.insert(0, PREFERRED_MODEL)
    return list(dict.fromkeys(models))


def pick_model(env):
    return candidate_models(env)[0]


def _strip_private(text):
    """Don't send phone numbers to a free-tier API."""
    return re.sub(r"(?m)^\s*phone:.*$\n?", "", re.sub(r"\+?\d[\d \-]{8,}\d", "[phone removed]", text))


def _generate(env, model, system, prompt, schema=None):
    config = {"temperature": 0.4}
    if schema:
        config.update(responseMimeType="application/json", responseSchema=schema)
    data = _request(env, f"models/{model}:generateContent", {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": config,
    })
    candidates = data.get("candidates") or []
    if not candidates or "content" not in candidates[0]:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or (candidates[0].get("finishReason") if candidates else "empty")
        raise RuntimeError(f"Gemini returned no content ({reason})")
    return "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []))


def _generate_any(env, system, prompt, schema=None):
    """Try models in order until one isn't busy; remember the one that worked."""
    errors = []
    for model in candidate_models(env)[:4]:
        try:
            text = _generate(env, model, system, prompt, schema)
        except Busy as e:
            errors.append(f"{model}: busy")
            continue
        if model != _model_cache["list"][0] and model in _model_cache["list"]:
            _model_cache["list"].remove(model)
            _model_cache["list"].insert(0, model)
        return text, model
    raise RuntimeError("all Gemini models busy (" + "; ".join(errors) + ")")


def _system(resume_text, profile_text):
    return _strip_private(f"<resume>\n{resume_text}\n</resume>\n\n<candidate_details>\n{profile_text}\n</candidate_details>")


def _job_block(job):
    return (f"<job>\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
            f"Salary: {job.get('salary') or 'not listed'}\n\n{job['description'][:8000]}\n</job>")


def analyze_job(env, job, resume_text, profile_text, effort="medium"):
    text, m = _generate_any(env, _system(resume_text, profile_text),
                            f"{INSIGHT_INSTRUCTIONS}\n\n{_job_block(job)}", INSIGHT_SCHEMA)
    out = json.loads(text)
    out["source"] = f"Gemini ({m})"
    return out


def cover_letter(env, job, resume_text, profile_text):
    # The phone number isn't sent, so the caller appends it to the signature.
    prompt = COVER_INSTRUCTIONS.replace("name, phone and LinkedIn", "name and LinkedIn")
    text, _ = _generate_any(env, _system(resume_text, profile_text), f"{prompt}\n\n{_job_block(job)}")
    return text.strip()
