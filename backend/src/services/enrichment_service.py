"""
Enrichment service — find the founder behind a domain.

Best-effort, brutally honest about confidence. NEVER fabricates a confident
email: a pattern-guessed address must come back with low confidence. Degrades
to {} when no Claude keys are configured (honest — no fake contact theater).
"""
from __future__ import annotations

from loguru import logger

from ..core.llm import claude_json, key_pool

_ENRICHMENT_SYSTEM = (
    "You are a contact-enrichment agent for AgentReady. Given a company domain, "
    "find the most senior reachable decision-maker (founder/CEO > CTO > head of "
    "eng): their name, email, and title — best-effort and brutally honest about "
    "confidence.\n\n"
    "Hard rules:\n"
    "- NEVER fabricate a confident email. If you did not actually see the address "
    "and are only inferring a pattern (first@, first.last@, flast@), set "
    "confidence <= 0.5 and say 'pattern guess — UNVERIFIED' in evidence.\n"
    "- A verified email found on the site (mailto:/team page) → confidence "
    "0.85-0.95.\n"
    "- A name found but email only pattern-inferred → confidence 0.4-0.6.\n"
    "- No human found → empty name/email, confidence <= 0.2, explain why.\n"
    "- `evidence` must state HOW each field was found (which page/mailto, or "
    "which pattern was inferred from what). Honest beats optimistic — a wrong "
    "confident email burns the domain."
)


def _clamp_confidence(v) -> float:
    try:
        c = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


def _looks_like_email(s: str) -> bool:
    return bool(s) and "@" in s and "." in s.split("@")[-1]


async def enrich_founder(domain: str) -> dict:
    """Best-effort find the founder behind ``domain``.

    Returns ``{founder_name, founder_email, founder_title, confidence,
    evidence}``. Any field may be empty / None when unknown. Returns ``{}`` when
    no Claude keys are configured or the LLM call fails — the caller then leaves
    the company un-contacted (honest, no email theater).
    """
    if not key_pool.has_keys():
        logger.info(f"[enrichment] no Claude keys — skipping enrichment for {domain}")
        return {}

    prompt = (
        f"Domain: {domain}\n\n"
        "Find the founder / most-senior reachable decision-maker behind this "
        "domain, following the enrichment methodology (check /about, /team, "
        "/leadership, /contact, footer, legal pages; reason from public knowledge "
        "for well-known startups; infer email pattern only with honest low "
        "confidence).\n\n"
        "Output ONLY this JSON object:\n"
        '{"founder_name":"","founder_email":"","founder_title":"",'
        '"confidence":0.0,"evidence":""}'
    )

    try:
        data = await claude_json(prompt, system=_ENRICHMENT_SYSTEM, max_tokens=1200)
    except Exception as exc:  # defensive — never propagate
        logger.warning(f"[enrichment] LLM call failed for {domain}: {exc}")
        return {}

    if not isinstance(data, dict) or not data:
        return {}

    name = str(data.get("founder_name", "") or "").strip()
    email = str(data.get("founder_email", "") or "").strip()
    title = str(data.get("founder_title", "") or "").strip()
    confidence = _clamp_confidence(data.get("confidence", 0.0))
    evidence = str(data.get("evidence", "") or "").strip()

    # Drop a malformed email rather than persist garbage.
    if email and not _looks_like_email(email):
        evidence = (evidence + " | dropped malformed email").strip(" |")
        email = ""

    return {
        "founder_name": name or None,
        "founder_email": email or None,
        "founder_title": title or None,
        "confidence": confidence,
        "evidence": evidence,
    }
