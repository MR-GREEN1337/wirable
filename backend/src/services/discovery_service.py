"""
Discovery service — the top of the autonomous agency funnel.

Given a category (e.g. "developer tools", "fintech"), propose real SaaS
companies that are LIKELY to score poorly on agent-readiness (no public API/MCP,
CAPTCHA-walled signup, docs-only, human-in-the-loop core action) — the best
targets for an audit + cold-email lead magnet.

Degrades gracefully: with no Claude keys (or on any LLM failure) it falls back
to a small curated per-category seed list so the demo still works offline.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import AsyncSessionLocal
from ..core.llm import claude_json, key_pool

# ---------------------------------------------------------------------------
# Curated offline seed list (real companies, UI-first / agent-unfriendly tells).
# Used when no Claude keys are configured or the LLM call fails. Keyed by a
# normalized category token; common synonyms map onto the same bucket.
# ---------------------------------------------------------------------------
_SEED_TARGETS: dict[str, list[dict]] = {
    "devtools": [
        {"domain": "retool.com", "name": "Retool", "reason": "Internal-tools builder is UI-first; core workflow lives in a drag-drop canvas with no agent-facing MCP surface."},
        {"domain": "postman.com", "name": "Postman", "reason": "API client is GUI-centric; agent automation requires the desktop app, no public MCP/llms.txt."},
        {"domain": "circleci.com", "name": "CircleCI", "reason": "CI dashboard is click-through; pipeline config + auth are UI-gated with no machine-readable agent entry point."},
        {"domain": "sentry.io", "name": "Sentry", "reason": "Error-monitoring console is dashboard-driven; signup is email-verify gated and there's no /llms.txt."},
        {"domain": "launchdarkly.com", "name": "LaunchDarkly", "reason": "Feature-flag management is UI-led; flag toggles happen in a console, no public MCP server."},
    ],
    "fintech": [
        {"domain": "brex.com", "name": "Brex", "reason": "Corporate-card dashboard is UI-only with SMS-OTP login; no public API surface an agent can self-serve."},
        {"domain": "ramp.com", "name": "Ramp", "reason": "Spend-management product is dashboard-centric; onboarding requires KYC + human review, no agent entry point."},
        {"domain": "mercury.com", "name": "Mercury", "reason": "Banking dashboard is UI-first with CAPTCHA + MFA signup; no /openapi.json or /llms.txt for agents."},
        {"domain": "bill.com", "name": "BILL", "reason": "AP/AR automation is click-through; the core invoice-approval action requires a human in the UI."},
        {"domain": "expensify.com", "name": "Expensify", "reason": "Expense reports are UI-driven; signup is email-verify gated and there's no public MCP surface."},
    ],
    "nocode": [
        {"domain": "webflow.com", "name": "Webflow", "reason": "Visual site builder — the entire product is a drag-drop canvas with no agent-usable API for the core build action."},
        {"domain": "bubble.io", "name": "Bubble", "reason": "No-code app builder is UI-only; app logic is wired visually with no machine surface or /llms.txt."},
        {"domain": "airtable.com", "name": "Airtable", "reason": "Database-spreadsheet hybrid is dashboard-led; signup is email-verify gated, no public MCP server."},
        {"domain": "notion.so", "name": "Notion", "reason": "Docs/DB workspace is UI-centric; core editing is human-in-the-loop with no agent-readable entry point."},
        {"domain": "softr.io", "name": "Softr", "reason": "No-code portal builder is fully visual; no public API/docs link in nav and signup is gated."},
    ],
    "crm": [
        {"domain": "pipedrive.com", "name": "Pipedrive", "reason": "Sales CRM is pipeline-UI driven; deal moves happen by drag-drop with no public MCP surface."},
        {"domain": "close.com", "name": "Close", "reason": "Sales CRM is dashboard-centric; signup is email-verify gated and there's no /llms.txt for agents."},
        {"domain": "copper.com", "name": "Copper", "reason": "CRM is UI-only inside Gmail; the core relationship-logging action requires a human in the UI."},
        {"domain": "insightly.com", "name": "Insightly", "reason": "CRM/PM tool is click-through; no machine-readable agent surface, dashboard-gated workflows."},
        {"domain": "nutshell.com", "name": "Nutshell", "reason": "SMB CRM is UI-first; signup is gated and there's no public API/MCP entry point for agents."},
    ],
    "analytics": [
        {"domain": "mixpanel.com", "name": "Mixpanel", "reason": "Product analytics is report-builder UI; insights require human-driven dashboard exploration, no /llms.txt."},
        {"domain": "amplitude.com", "name": "Amplitude", "reason": "Analytics product is chart-builder driven; core analysis is human-in-the-loop with no agent MCP surface."},
        {"domain": "heap.io", "name": "Heap", "reason": "Autocapture analytics is dashboard-led; signup is email-verify gated, no public machine surface."},
        {"domain": "hotjar.com", "name": "Hotjar", "reason": "Heatmap/session product is purely visual; insights live in a UI with no agent-readable entry point."},
        {"domain": "looker.com", "name": "Looker", "reason": "BI platform is explore-UI driven; LookML + dashboards are human-operated, no public MCP server."},
    ],
}

# Aliases → canonical seed bucket.
_CATEGORY_ALIASES: dict[str, str] = {
    "developer tools": "devtools",
    "developer-tools": "devtools",
    "dev tools": "devtools",
    "devtools": "devtools",
    "engineering": "devtools",
    "fintech": "fintech",
    "finance": "fintech",
    "financial": "fintech",
    "fintech apis": "fintech",
    "no-code": "nocode",
    "no code": "nocode",
    "nocode": "nocode",
    "low-code": "nocode",
    "crm": "crm",
    "sales": "crm",
    "sales crm": "crm",
    "analytics": "analytics",
    "data": "analytics",
    "bi": "analytics",
    "product analytics": "analytics",
}

_DEFAULT_SEED_BUCKET = "devtools"


def _seed_for(category: str, count: int) -> list[dict]:
    """Return up to ``count`` curated seed targets for a category (offline path)."""
    bucket = _CATEGORY_ALIASES.get((category or "").strip().lower(), _DEFAULT_SEED_BUCKET)
    targets = _SEED_TARGETS.get(bucket, _SEED_TARGETS[_DEFAULT_SEED_BUCKET])
    return [dict(t) for t in targets[: max(0, count)]]


def _normalise_domain(raw: str) -> str:
    """Strip scheme + path, lowercase. Mirrors the audit endpoint normaliser."""
    return (
        (raw or "")
        .lower()
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .removeprefix("www.")
        .split("/")[0]
        .split("?")[0]
        .strip()
    )


_DISCOVERY_SYSTEM = (
    "You are a B2B prospecting agent for AgentReady. Your job is to surface real, "
    "currently-operating SaaS companies that are LIKELY to score poorly on "
    "agent-readiness — products an autonomous agent probably CANNOT use: no public "
    "API/MCP server, no /llms.txt or /openapi.json, CAPTCHA/email-verify/SMS-OTP "
    "signup, UI-only core action (human-in-the-loop). These are the best targets "
    "for an audit + outbound lead magnet.\n\n"
    "Hard rules:\n"
    "- REAL companies with REAL apex domains only (e.g. acme.com). No invented names, "
    "no example.com placeholders.\n"
    "- Skip companies that are already agent-native and would score HIGH (Stripe, "
    "Twilio, GitHub, Vercel, Linear, OpenAI, etc.) — they're bad targets.\n"
    "- Each `reason` is ONE concrete line tied to a specific agent-readiness tell "
    "(UI-only, CAPTCHA signup, no docs, no MCP), not 'popular app'.\n"
    "- `domain` is the bare apex, no scheme, no path."
)


async def _llm_discover(category: str, count: int) -> list[dict]:
    """Ask Claude for ``count`` candidate targets. Returns [] on any failure."""
    prompt = (
        f"Category / seed segment: {category!r}\n"
        f"Propose {count} real SaaS companies in this segment that LIKELY have poor "
        f"agent-readiness, following the methodology.\n\n"
        "Output ONLY this JSON object:\n"
        '{"targets":[{"domain":"acme.com","name":"Acme","reason":"<one concrete '
        'agent-readiness tell>"}]}'
    )
    data = await claude_json(prompt, system=_DISCOVERY_SYSTEM, max_tokens=2000)
    raw = data.get("targets") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        domain = _normalise_domain(str(t.get("domain", "")))
        if not domain or "." not in domain:
            continue
        out.append(
            {
                "domain": domain,
                "name": (str(t.get("name", "")).strip() or domain),
                "reason": str(t.get("reason", "")).strip(),
            }
        )
    return out


async def _existing_domains(db: AsyncSession, domains: list[str]) -> set[str]:
    """Return the subset of ``domains`` that already have a Company row."""
    from ..models.company import Company

    if not domains:
        return set()
    result = await db.execute(select(Company.domain).where(Company.domain.in_(domains)))
    return {row[0] for row in result.all()}


async def discover_targets(
    category: str,
    count: int = 5,
    db: Optional[AsyncSession] = None,
) -> list[dict]:
    """Propose ``count`` NEW SaaS targets in ``category`` likely to score poorly.

    Returns a list of ``{"domain", "name", "reason"}`` dicts, de-duplicated
    against existing Company rows by domain. Uses Claude when keys are
    configured, otherwise a curated per-category seed list so the loop still
    works offline.

    A ``db`` session may be passed (e.g. by the scout, to dedup within its own
    transaction); when omitted a short-lived AsyncSessionLocal is opened just for
    the dedup read.
    """
    # 1. Propose candidates (LLM if we have keys, else curated seeds).
    candidates: list[dict] = []
    if key_pool.has_keys():
        try:
            candidates = await _llm_discover(category, count)
        except Exception as exc:  # defensive — never propagate
            logger.warning(f"[discovery] LLM discovery failed: {exc}")
            candidates = []
    if not candidates:
        logger.info(f"[discovery] using curated seed list for category={category!r}")
        candidates = _seed_for(category, count)

    # 2. De-dup within the proposed batch (first occurrence wins).
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        d = c.get("domain", "")
        if d and d not in seen:
            seen.add(d)
            deduped.append(c)

    if not deduped:
        return []

    # 3. De-dup against existing Company rows.
    async def _filter(session: AsyncSession) -> list[dict]:
        existing = await _existing_domains(session, [c["domain"] for c in deduped])
        return [c for c in deduped if c["domain"] not in existing]

    try:
        if db is not None:
            new_targets = await _filter(db)
        else:
            async with AsyncSessionLocal() as session:
                new_targets = await _filter(session)
    except Exception as exc:  # if dedup read fails, don't lose the whole batch
        logger.warning(f"[discovery] dedup read failed ({exc}); returning unfiltered")
        new_targets = deduped

    return new_targets[: max(0, count)]
