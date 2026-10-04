"""Built-in interview prep, used when no ANTHROPIC_API_KEY is set (or the AI call fails).

Each role category has likely questions, topics to revise and free resources.
`offline_insight()` returns the same shape as the AI insight in ai.py so the
email template doesn't care where the prep came from.
"""

import re

# Technologies worth flagging as a "gap" when a job asks for them and the profile lacks them.
TECH_TERMS = [
    "python", "go", "golang", "java", "node.js", "typescript", "rust", "c++", "scala",
    "flask", "fastapi", "django", "celery", "grpc", "graphql", "rest api", "microservices",
    "sql", "postgresql", "mysql", "mongodb", "redis", "kafka", "rabbitmq", "elasticsearch",
    "aws", "azure", "gcp", "lambda", "ec2", "s3", "terraform", "ansible", "pulumi",
    "cloudformation", "bicep", "docker", "kubernetes", "helm", "argocd", "jenkins",
    "github actions", "gitlab ci", "ci/cd", "prometheus", "grafana", "datadog", "linux",
    "bash", "vault", "oauth", "airflow", "spark", "pandas", "pytorch", "llm", "langchain",
    "system design", "distributed systems",
]

CATEGORIES = {
    "python_backend": {
        "match": ["backend", "back-end", "python", "api", "django", "flask", "fastapi", "software engineer", "developer"],
        "questions": [
            ("Walk me through how a request flows through one of your Flask microservices, end to end.",
             "Use Harmonize: routing, auth, Vault/Key Vault secret fetch, state machine step, response, logging."),
            ("How would you design a REST API for a long-running job (e.g. VM provisioning)?",
             "202 Accepted + job id, polling or webhooks, idempotency keys, status endpoint."),
            ("Explain the GIL. When would you use threads vs processes vs asyncio?",
             "I/O-bound → threads/asyncio; CPU-bound → multiprocessing."),
            ("How do you make a call to a flaky downstream API reliable?",
             "Your Tufin story: retries, exponential backoff + jitter, timeouts, circuit breaker, alerting."),
            ("How would you cache an expensive result, and how do you invalidate it?",
             "Your Redis-cached Vault-signed SSH keys with a 36h TTL."),
            ("SQL: find the second-highest salary per department.",
             "Window functions: DENSE_RANK() OVER (PARTITION BY dept ORDER BY salary DESC)."),
            ("What's the difference between a decorator and a context manager? Write one of each.", ""),
        ],
        "topics": ["Python internals (GIL, generators, decorators, context managers)", "REST design & HTTP status codes",
                   "SQL joins, indexes, window functions", "Caching patterns (Redis, TTL, invalidation)",
                   "Microservice communication & idempotency", "Unit testing with pytest & mocking",
                   "DSA basics: arrays, hashing, two pointers, BFS/DFS (LeetCode easy–medium)"],
        "resources": [
            ("NeetCode 150 (DSA practice)", "https://neetcode.io/practice"),
            ("Real Python: intermediate topics", "https://realpython.com/tutorials/intermediate/"),
            ("System Design Primer", "https://github.com/donnemartin/system-design-primer"),
            ("SQLBolt / LeetCode SQL 50", "https://leetcode.com/studyplan/top-sql-50/"),
        ],
    },
    "cloud_devops": {
        "match": ["cloud", "devops", "azure", "aws", "gcp", "automation", "infrastructure", "terraform"],
        "questions": [
            ("How have you automated VM lifecycle across Azure and AWS? What broke and how did you fix it?",
             "Harmonize: provisioning → config → validation → decommission; 5,000+ requests/month."),
            ("How do you manage secrets in a cloud application?",
             "Vault dynamic/ephemeral credentials, Azure Key Vault, rotation, no static keys in code."),
            ("Explain OAuth 2.0 client credentials flow and when you'd use it.",
             "Your Basic Auth → OAuth migration: token caching, refresh, scopes."),
            ("What is IMDS and why would a VM use it?", "Your zone placement / VM detection work."),
            ("Terraform vs ARM/Bicep vs scripting with SDKs: when would you pick each?",
             "Declarative state vs imperative automation; be honest if Terraform is new to you."),
            ("Design a CI/CD pipeline for a Python microservice deployed to Kubernetes.",
             "Lint/test → build image → scan → push → ArgoCD GitOps sync → canary/rollback."),
            ("A VM provisioning request is stuck. How do you debug it?",
             "Logs, correlation ids, state machine step, cloud activity log, retry safely."),
        ],
        "topics": ["Azure: ARM, RBAC, Key Vault, VNets, Azure Arc", "AWS: IAM, EC2, VPC, S3, Lambda",
                   "Infrastructure as Code (Terraform basics)", "Docker & Kubernetes fundamentals",
                   "CI/CD (Jenkins, GitHub Actions, ArgoCD)", "Secrets management & OAuth 2.0",
                   "Linux & networking basics (DNS, TCP, SSH)"],
        "resources": [
            ("Microsoft Learn: AZ-204 path", "https://learn.microsoft.com/en-us/credentials/certifications/azure-developer/"),
            ("AWS Skill Builder (free courses)", "https://skillbuilder.aws/"),
            ("Terraform tutorials", "https://developer.hashicorp.com/terraform/tutorials"),
            ("Kubernetes basics", "https://kubernetes.io/docs/tutorials/kubernetes-basics/"),
        ],
    },
    "sre_platform": {
        "match": ["sre", "site reliability", "platform", "reliability", "observability"],
        "questions": [
            ("What are SLIs, SLOs and error budgets? Define one for a provisioning API.", ""),
            ("Tell me about an incident you handled. What was the root cause and what did you change?",
             "Tufin outage: ~10 hours of downtime saved per outage with retries + ticketing + alert routing."),
            ("How would you reduce toil for an on-call team?", "Automation, runbooks, alert quality."),
            ("How do you monitor a fleet of microservices?", "Metrics, logs, traces; Prometheus/Grafana; RED/USE."),
            ("What happens when you type a URL into a browser?", "DNS, TCP/TLS, HTTP, load balancer, app, DB."),
        ],
        "topics": ["SLIs/SLOs/error budgets", "Incident response & postmortems", "Observability (metrics, logs, traces)",
                   "Linux troubleshooting", "Kubernetes operations", "Capacity planning"],
        "resources": [
            ("Google SRE book (free)", "https://sre.google/sre-book/table-of-contents/"),
            ("Linux troubleshooting: SadServers", "https://sadservers.com/"),
        ],
    },
    "data_ai": {
        "match": ["data", "machine learning", "ml", "ai", "llm", "analytics", "nlp"],
        "questions": [
            ("Tell me about your M.Tech coursework/projects in AI & Data Science.", ""),
            ("How would you build and serve a model behind a REST API?", "FastAPI, batching, versioning, monitoring drift."),
            ("Explain bias–variance tradeoff, and precision vs recall.", ""),
            ("How would you build a RAG pipeline over internal documents?", "Chunking, embeddings, vector DB, eval."),
            ("Pandas: how would you clean and aggregate messy data?", "Your SPPU Result Converter project."),
        ],
        "topics": ["ML fundamentals & metrics", "Pandas / NumPy", "Model serving & MLOps", "LLMs, embeddings, RAG",
                   "SQL for analytics", "Statistics basics"],
        "resources": [
            ("Google ML Crash Course", "https://developers.google.com/machine-learning/crash-course"),
            ("Made With ML (MLOps)", "https://madewithml.com/"),
        ],
    },
}

BEHAVIORAL = [
    ("Tell me about yourself.", "90 seconds: 2 yrs at Relevance Lab → multi-cloud automation → Vault/OAuth security wins → M.Tech at IIT Patna → why this role."),
    ("Why are you looking for a change?", "Growth, scope, stay positive about your current employer."),
]


def contains(text, term):
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def pick_categories(title, description):
    title, text = title.lower(), f"{title} {description}".lower()
    scored = []
    for name, cat in CATEGORIES.items():
        score = sum(3 for k in cat["match"] if contains(title, k)) + sum(1 for k in cat["match"] if contains(text, k))
        scored.append((score, name))
    scored.sort(reverse=True)
    top = [n for s, n in scored[:2] if s > 0]
    return top or ["python_backend"]


def offline_insight(job, profile_skills):
    """Rule-based fallback with the same keys as ai.JobInsight."""
    text = f"{job['title']} {job['description']}".lower()
    mine = {s.lower() for s in profile_skills}
    asked = [t for t in TECH_TERMS if contains(text, t)]
    matched = [t for t in asked if t in mine or any(t in m or m in t for m in mine)]
    missing = [t for t in asked if t not in matched]

    cats = pick_categories(job["title"], job["description"])
    questions, topics, resources = [], [], []
    for name in cats:
        c = CATEGORIES[name]
        questions += [{"question": q, "hint": h} for q, h in c["questions"][:4]]
        topics += c["topics"][:4]
        resources += [{"title": t, "url": u} for t, u in c["resources"][:2]]
    questions += [{"question": q, "hint": h} for q, h in BEHAVIORAL]
    for skill in missing[:3]:
        topics.insert(0, f"{skill} (asked in this job, not on your resume): learn the basics")

    snippet = job["description"][:280].rsplit(" ", 1)[0] + "…" if len(job["description"]) > 280 else job["description"]
    return {
        "summary": snippet,
        "key_requirements": asked[:8],
        "matched_skills": matched[:10],
        "missing_skills": missing[:6],
        "fit_verdict": "",
        "how_to_pitch": "",
        "interview_questions": questions[:8],
        "topics_to_revise": topics[:7],
        "resources": resources[:4],
        "source": "built-in library",
    }
