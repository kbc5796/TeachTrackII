"""
Categorizes a job and pulls out the fields the research doc asks for:
skills, technologies, education, responsibilities, requirements.

categorize_job() is kept so older code still works.
"""
import re

# ---------------------------------------------------------------
# Categories. A job can match several; the best match is primary_category.
# ---------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "AI": [
        "artificial intelligence", "machine learning", "deep learning",
        "generative ai", "genai", "large language model",
        "large language models", "llm", "llms", "foundation models",
        "natural language processing", "nlp", "computer vision",
        "neural network", "neural networks", "reinforcement learning",
        "mlops", "ml engineer", "ai engineer", "applied scientist",
        "research scientist", "prompt engineering", "foundation model",
        "pytorch", "tensorflow", "hugging face", "langchain",
    ],
    "Software Engineering": [
        "software engineer", "software developer", "backend", "frontend",
        "full stack", "full-stack", "devops", "site reliability",
        "api", "microservices",
    ],
    "Data": [
        "data engineer", "data analyst", "data scientist", "analytics",
        "sql", "data warehouse", "business intelligence", "etl",
    ],
    "Cybersecurity": [
        "security engineer", "cybersecurity", "infosec",
        "penetration testing", "threat", "vulnerability",
    ],
    "Cloud & Infrastructure": [
        "cloud", "aws", "azure", "gcp", "kubernetes", "infrastructure",
        "network engineer",
    ],
}

# Skills = abilities/concepts.  Technologies = named tools/languages.
SKILLS = [
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "data analysis", "data engineering", "statistics",
    "software testing", "code review", "agile", "scrum",
    "project management", "system design", "distributed systems",
    "microservices", "rest api", "ci/cd", "devops", "cybersecurity",
    "penetration testing", "cloud computing", "networking",
    "object-oriented programming", "algorithms", "data structures",
    "prompt engineering", "mlops", "etl", "data modeling",
    "technical writing", "communication", "leadership",
]

TECHNOLOGIES = [
    "python", "java", "javascript", "typescript", "c++", "c#", "golang",
    "rust", "scala", "kotlin", "swift", "sql", "nosql", "matlab",
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes",
    "terraform", "git", "github", "jenkins", "linux", "react", "angular",
    "node.js", "spring boot", "django", "flask", ".net", "pytorch",
    "tensorflow", "scikit-learn", "pandas", "numpy", "spark", "hadoop",
    "kafka", "snowflake", "databricks", "tableau", "power bi",
    "salesforce", "sap", "oracle", "postgresql", "mysql", "mongodb",
    "redis", "elasticsearch", "hugging face", "langchain", "openai", "jira",
]

# Words are case-insensitive via (?i:...); abbreviations (BS, MS, MBA) must be
# uppercase so "associate", "Scrum Master" and "MS Office" don't match.
EDUCATION_PATTERNS = {
    "Associate": r"(?i:associate'?s? degree|associate of (?:arts|science))",
    "Bachelor's": (
        r"(?i:bachelor'?s?|undergraduate degree)"
        r"|\b(?:BS|B\.S\.?|BA|B\.A\.?|BSc)\b"
    ),
    "Master's": (
        r"(?i:master'?s(?: degree)?|master of)"
        r"|\b(?:MS(?! (?:Office|Excel|Word|Project|Teams|SQL|Access))|M\.S\.?|MSc|MBA)\b"
    ),
    "PhD": r"(?i:ph\.?\s?d\.?|doctorate|doctoral)",
}


def _term_regex(term):
    # (?<![\w+#]) / (?![\w+#]) keeps "java" from matching "javascript"
    # and "c" style terms from matching inside other words.
    return re.compile(
        r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", re.IGNORECASE
    )


_CATEGORY_REGEX = {
    cat: [_term_regex(k) for k in kws]
    for cat, kws in CATEGORY_KEYWORDS.items()
}
_SKILL_REGEX = [(t, _term_regex(t)) for t in SKILLS]
_TECH_REGEX = [(t, _term_regex(t)) for t in TECHNOLOGIES]
_EDU_REGEX = {
    label: re.compile(p)
    for label, p in EDUCATION_PATTERNS.items()
}


def _count(regexes, text):
    return sum(1 for rx in regexes if rx.search(text))


def _find(pairs, text):
    return sorted({term for term, rx in pairs if rx.search(text)})


# Section headings must sit on their own line (optionally ending in ":"),
# so the word "responsibilities" inside a sentence doesn't start a section.
_RESP_HEAD = (
    r"(?:key )?responsibilities|what you(?:'|’)?ll (?:be )?do(?:ing)?"
    r"|what you will (?:be )?do(?:ing)?|a typical day|key duties|day[- ]to[- ]day"
)
_REQ_HEAD = (
    r"(?:minimum |basic |preferred |required )?qualifications|requirements"
    r"|what we need to see|what you(?:'|’)?ll bring|what you bring"
    r"|your expertise|who you are|required skills"
)
_STOP_HEAD = (
    r"ways to stand out(?: from the crowd)?|our commitment.*"
    r"|how we(?:'|’)?ll take care of you|benefits|equal opportunity.*"
    r"|your location|about (?:us|the team)"
)


def _heading(pattern):
    return re.compile(
        r"^[ \t\-•*]*(?:" + pattern + r")[ \t]*:?[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )


RESP_START = _heading(_RESP_HEAD)
REQ_START = _heading(_REQ_HEAD)
RESP_END = _heading(_REQ_HEAD + "|" + _STOP_HEAD)
REQ_END = _heading(_RESP_HEAD + "|" + _STOP_HEAD)


def _section(text, start_rx, end_rx, limit=1500):
    """Best-effort: text under a heading like 'Responsibilities' up to the
    next heading. Needs line breaks, so Custom page text (collapsed to one
    line) will usually return ''."""
    start = start_rx.search(text)
    if not start:
        return ""
    rest = text[start.end():]
    end = end_rx.search(rest)
    chunk = rest[: end.start()] if end else rest
    return re.sub(r"\s+", " ", chunk).strip()[:limit]


def analyze_job(title, body_text):
    title = title or ""
    body_text = body_text or ""
    title_l = title.lower()
    body_l = body_text.lower()

    scores = {}
    ai_signal = ""
    for cat, regexes in _CATEGORY_REGEX.items():
        t_hits = _count(regexes, title_l)
        b_hits = _count(regexes, body_l)

        # AI needs stronger evidence: company boilerplate such as "leading
        # the way in Artificial Intelligence" appears on non-AI jobs too.
        needed = 3 if cat == "AI" else 2

        if cat == "AI":
            # Uppercase "AI" / "ML" only, so "email" or "mail" never match.
            if re.search(r"\b(AI|ML)\b", title):
                t_hits += 1
            if len(re.findall(r"\bAI\b", body_text)) >= 3:
                b_hits += 1

        # A hit in the title, or enough different hits in the text.
        if t_hits >= 1 or b_hits >= needed:
            scores[cat] = t_hits * 3 + b_hits
            if cat == "AI":
                ai_signal = "title" if t_hits >= 1 else "description"

    categories = sorted(scores, key=scores.get, reverse=True) or ["Other"]

    combined = title_l + " " + body_l
    # Education uses the original capitalisation (BS / MS / MBA are uppercase).
    original = title + " " + body_text
    education = [
        label for label, rx in _EDU_REGEX.items() if rx.search(original)
    ]

    return {
        "categories": categories,
        "primary_category": categories[0],
        "is_ai_job": "AI" in categories,
        "ai_signal": ai_signal,   # "title" = strong, "description" = weaker
        "skills": _find(_SKILL_REGEX, combined),
        "technologies": _find(_TECH_REGEX, combined),
        "education": education,
        "responsibilities": _section(body_text, RESP_START, RESP_END),
        "requirements": _section(body_text, REQ_START, REQ_END),
    }


def categorize_job(title, body_text):
    """Backward-compatible: returns just the category list."""
    return analyze_job(title, body_text)["categories"]