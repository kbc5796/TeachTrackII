"""Explainable weighted relevance, independent of company industry."""
import re

from .spiders.skill_categories import analyze_job

TITLE = re.compile(
    r"\b(software|backend|frontend|front.end|back.end|full.stack|mobile|embedded software|"
    r"qa automation|ai|machine learning|mlops|nlp|computer vision|data|database|analytics|"
    r"cybersecurity|security|application security|cloud|devops|site reliability|platform|"
    r"network|it|systems?|technical support)\b.*\b"
    r"(engineer|developer|scientist|analyst|administrator|intern|architect)\b|"
    r"\bpenetration tester\b", re.I)
EXCLUDE = re.compile(r"\b(cashier|retail associate|warehouse|sales|human resources|hr|marketing|"
                     r"nurse|accountant|mechanical|chemical|manufacturing)\b", re.I)
EVIDENCE = re.compile(r"\b(software development|programming|distributed systems|algorithms|"
                     r"data pipelines|network administration|system administration|"
                     r"penetration testing|incident response|computer science|software testing|"
                     r"machine learning|cloud infrastructure)\b", re.I)


def score_job(title, description, responsibilities="", requirements="", threshold=0.6):
    if not 0 <= threshold <= 1:
        raise ValueError("CS threshold must be between zero and one")
    text = "\n".join((description, responsibilities, requirements))
    technologies = analyze_job("", text)["technologies"]
    evidence = len(set(m.group().lower() for m in EVIDENCE.finditer(text)))
    title_hit = bool(TITLE.search(title))
    excluded = bool(EXCLUDE.search(title))
    score = min(1.0, (0.65 if title_hit else 0) + min(0.25, len(technologies) * 0.05)
                + min(0.35, evidence * 0.12))
    if excluded:
        score = min(score, 0.25)
    ambiguous = bool(re.search(r"data analyst|security analyst|technical support", title, re.I))
    substantial = len(technologies) >= 2 and evidence >= 1
    accepted = score >= threshold and not excluded and (not ambiguous or substantial)
    return {"is_cs_job": accepted, "cs_relevance_score": round(score, 3),
            "cs_filter_reason": f"technical_title={title_hit}; technologies={len(technologies)}; "
            f"technical_concepts={evidence}; excluded_title={excluded}; threshold={threshold}"}
