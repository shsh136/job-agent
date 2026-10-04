"""Claude-powered job analysis, interview prep and cover letters.

Everything here is optional: if ANTHROPIC_API_KEY is missing or the `anthropic`
package isn't installed, `available()` returns False and the agent falls back to
prep_library.py.
"""

from pydantic import BaseModel

try:
    import anthropic
except ImportError:  # agent still works without AI
    anthropic = None

MODEL = "claude-opus-5-5"
# On a safety-classifier refusal, the API retries on a fallback model in the same call.
FALLBACK_KW = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class Question(BaseModel):
    question: str
    hint: str  # how the candidate should answer, using their own experience


class Resource(BaseModel):
    title: str
    url: str


class JobInsight(BaseModel):
    summary: str
    key_requirements: list[str]
    matched_skills: list[str]
    missing_skills: list[str]
    fit_verdict: str
    how_to_pitch: str
    interview_questions: list[Question]
    topics_to_revise: list[str]
    resources: list[Resource]


INSIGHT_INSTRUCTIONS = """You are a career coach helping the candidate below decide whether to apply to a job and prepare for its interview.

Return:
- summary: 2 sentences on what the role actually does day to day.
- key_requirements: the 5-8 must-have requirements from the posting.
- matched_skills: requirements the candidate clearly meets (from the resume only).
- missing_skills: requirements the candidate lacks or that the resume doesn't show.
- fit_verdict: one honest line, e.g. "Strong fit: apply today" or "Stretch: asks 5 yrs, apply only if you can show X".
- how_to_pitch: 1-2 sentences on which resume achievements to lead with for this role.
- interview_questions: 8 questions this company is likely to ask (technical for this stack, plus 2 behavioral). For each, a short hint tying the answer to the candidate's real experience.
- topics_to_revise: 5-7 topics, gaps first.
- resources: 3-4 free, well-known learning resources with real URLs (official docs, NeetCode, Microsoft Learn, etc.). Do not invent URLs.

Never claim the candidate has a skill the resume doesn't show."""

COVER_INSTRUCTIONS = """Write a concise cover letter / application email body (150-220 words) from the candidate below for this job.
Plain text, no placeholders, no markdown. Open with the specific role and one concrete, relevant achievement with a number.
Map 2-3 of the job's requirements to real resume evidence. Close with availability (use the notice period if given) and a thank-you.
Do not claim any skill or experience that isn't in the resume. Sign off with the candidate's name, phone and LinkedIn."""


def available(env):
    return anthropic is not None and bool(env.get("ANTHROPIC_API_KEY"))


def _client(env):
    return anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"], max_retries=3)


def _system(resume_text, profile_text):
    # Stable across every job in a run, so it's cached after the first call.
    return [{
        "type": "text",
        "text": f"<resume>\n{resume_text}\n</resume>\n\n<candidate_details>\n{profile_text}\n</candidate_details>",
        "cache_control": {"type": "ephemeral"},
    }]


def _job_block(job):
    return (f"<job>\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
            f"Salary: {job.get('salary') or 'not listed'}\n\n{job['description'][:8000]}\n</job>")


def analyze_job(env, job, resume_text, profile_text, effort="medium"):
    """Returns a dict shaped like JobInsight, or raises on API failure."""
    resp = _client(env).beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=_system(resume_text, profile_text),
        output_config={"effort": effort},
        output_format=JobInsight,
        messages=[{"role": "user", "content": f"{INSIGHT_INSTRUCTIONS}\n\n{_job_block(job)}"}],
        **FALLBACK_KW,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"no structured output (stop_reason={resp.stop_reason})")
    out = resp.parsed_output.model_dump()
    out["source"] = "Claude"
    return out


def cover_letter(env, job, resume_text, profile_text):
    resp = _client(env).beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_system(resume_text, profile_text),
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"{COVER_INSTRUCTIONS}\n\n{_job_block(job)}"}],
        **FALLBACK_KW,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("cover letter request was declined")
    return "".join(b.text for b in resp.content if b.type == "text").strip()
