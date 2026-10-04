# Job Search Agent

Every morning the agent searches job boards, scores each new opening against your profile, and emails
you a digest. Each job in the digest shows:

- **Details:** title, company, location, salary, job type, posted date, match score
- **Analysis:** what the role does, key requirements, skills you have, gaps, and how to pitch yourself
- **Interview prep:** about 8 likely questions with tips tied to your own experience, topics to revise, free resources
- **Buttons:** *Open job posting* · *Let agent apply* · *I applied myself* · *Skip*

Strong matches you haven't acted on show up again under "Still waiting for your decision" for 5 days.

## How applying works

Tapping **Let agent apply** opens a pre-filled email (`APPLY J7F3A`). Send it. Within 30 minutes the agent:

1. Writes a cover letter tailored to that job (Claude if `ANTHROPIC_API_KEY` is set, otherwise a template).
2. Fills in answers to the usual application-form questions from `profile.json`.
3. If the posting lists an apply email address, **emails your application to the employer** with your resume
   attached and you in CC. Otherwise it emails *you* an **application kit**: the apply link, the cover letter,
   the form answers and your resume, ready to paste in about 2 minutes.

The agent never applies to anything unless you've sent it `APPLY`. It can't log in to LinkedIn, Naukri, Workday
and similar sites for you: those sites need your login, CAPTCHAs and custom questions, and automated
submissions break their terms and can get your account flagged.

Reply commands (in the subject or on their own line in the body, sent from `email_to`):

| Command | Effect |
|---|---|
| `APPLY J7F3A` | Prepare and send the application (see above) |
| `APPLIED J7F3A` | Mark it applied (you did it yourself) |
| `SKIP J7F3A` | Not interested; stops reminders |
| `INTERVIEW J7F3A` / `REJECTED J7F3A` / `OFFER J7F3A` | Track progress |

## Setup (one time)

1. **Install and copy the templates:**
   ```powershell
   pip install -r requirements.txt
   copy .env.example .env
   copy profile.example.json profile.json
   ```
   Both copies are git-ignored, so your passwords and personal details never leave your PC.
2. **Fill in `profile.json`:** current city, CTC, expected CTC, notice period, and so on.
   The agent won't apply until those four fields are set.
3. **Fill in `.env`:**
   - `SMTP_USER` / `SMTP_APP_PASSWORD`: the Gmail account that sends the emails and receives your replies,
     plus a Gmail **App Password** (Google Account → Security → 2-Step Verification → App passwords).
     Use your own Gmail if you want employers to see applications coming from you.
     In Gmail settings → *Forwarding and POP/IMAP*, make sure **IMAP is enabled**.
   - `GEMINI_API_KEY` (free, recommended): tailored analysis, interview prep and cover letters via Google
     Gemini's free tier. Get it at https://aistudio.google.com/apikey. Your phone number is removed before
     anything is sent. `ANTHROPIC_API_KEY` (paid) is used instead if you set it.
   - `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` (free, https://developer.adzuna.com/) and `RAPIDAPI_KEY`
     (JSearch free tier: LinkedIn, Indeed and Naukri listings via Google Jobs). Without them you only get
     remote jobs from Remotive and Arbeitnow.
4. **Test it:**
   ```powershell
   python job_agent.py --dry-run   # writes digest.html, sends nothing
   python job_agent.py             # sends the digest for real
   ```
5. **Schedule it** (daily digest at 9 AM, plus a reply check every 30 minutes):
   ```powershell
   powershell -ExecutionPolicy Bypass -File register_task.ps1
   ```

## Other commands

```powershell
python job_agent.py apply J7F3A          # same as replying APPLY, but only emails you the kit
python job_agent.py apply J7F3A --send   # ...and emails the employer if the posting has an address
python job_agent.py mark J7F3A interview
python job_agent.py status               # your application tracker
python job_agent.py prep "SRE engineer" --company Google --email   # prep for any role
```

## Files

| File | What it is |
|---|---|
| `config.json` | Search queries, skill weights, score thresholds, AI settings |
| `profile.json` | Your application details (stays on your PC) |
| `jobs.json` | Jobs from past digests, so IDs keep working (45 days) |
| `applications.json` | Your application tracker |
| `applications/<ID>_<Company>/` | Cover letter, form answers and job snapshot for each application |
| `agent.log` | Log of every run |

## Tuning (`config.json`)

| Setting | What it does |
|---|---|
| `min_score` | Only jobs at or above this score are emailed (default 55) |
| `search_queries`, `skills`, `title_keywords` | What to search for and how matches are scored |
| `experience_years` | Jobs asking for more than this +2 get −20 |
| `remind_days`, `remind_min_score` | How long and how strong a match must be to keep reminding you |
| `ai.max_jobs_per_run` | How many top jobs get a Claude analysis each day (cost control); the rest use the built-in library |
| `ai.effort` | `low` / `medium` / `high`: how much reasoning Claude spends on each job |
| `resume_yaml` | Resume source for the AI (your portfolio's `resume.yml`) |

**Score** = skills (up to 60) + title fit (25) + experience fit (−20 to 15) − senior penalty (15).
