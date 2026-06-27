"""
CATTS — Consensus Aggregation Through Threshold Scoring.

Each agent returns a dict shaped like:
  {
    "domain": str,
    "dimensions": {
      "<dim>": {
        "passed": bool,
        "confidence": float,  # 0-1
        "evidence": str,
      },
      ...
    }
  }

catts_aggregate() merges N agent results into a single verdict,
returning None when confidence is too low and a 4th agent is needed.

catts_aggregate_with_arbiter() is the async, Claude-backed variant: for any
dimension where the agents split (margin < DELTA) and Claude keys are
available, it asks Claude to adjudicate from the diverging evidence; otherwise
it falls back to majority vote.
"""
from loguru import logger

from ..core.llm import key_pool
from ..core.llm.anthropic_client import claude_json
from ..core.contracts import DIMENSION_KEYS, DIMENSION_WEIGHTS

# Canonical 6 deterministic dimensions live in core.contracts (weights sum to
# 100). We re-export them here so existing callers keep working.
DIMENSIONS = list(DIMENSION_KEYS)
WEIGHTS: dict[str, int] = dict(DIMENSION_WEIGHTS)

# Minimum margin (|agree - disagree| / total) to consider consensus reached
DELTA: float = 0.7

import re as _re


def clean_evidence(text: object, limit: int = 180) -> str:
    """Sanitize a piece of evidence for display — never let a raw HTML error
    page (e.g. a CloudFront 403) leak into the UI. Collapses HTML to a short
    status hint, strips tags, collapses whitespace, truncates."""
    if not text:
        return ""
    s = str(text)
    low = s.lower()
    if "<html" in low or "<!doctype" in low or "cloudfront" in low:
        m = _re.search(r"(\d{3})\s*error", low) or _re.search(r"\b(4\d\d|5\d\d)\b", s)
        code = m.group(1) if m else ""
        return f"sandbox unavailable{(' — upstream ' + code) if code else ''}"
    s = _re.sub(r"<[^>]+>", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return (s[:limit].rstrip() + "…") if len(s) > limit else s


def catts_aggregate(results: list[dict]) -> dict | None:
    """
    Aggregate N audit-agent results with CATTS consensus logic.

    Args:
        results: list of per-agent dicts as described above.

    Returns:
        Aggregated {score, confidence, dimensions} dict, or None if
        confidence < 0.6 and fewer than 5 agents ran (caller should
        spawn an additional arbiter agent).
    """
    if not results:
        return None

    dims: dict[str, dict] = {}
    for dim in DIMENSIONS:
        votes: list[bool] = []
        evidences: list[str] = []

        for r in results:
            dim_data = r.get("dimensions", {}).get(dim, {})
            votes.append(bool(dim_data.get("passed", False)))
            ev = dim_data.get("evidence", "")
            if ev:
                evidences.append(ev)

        n = len(votes)
        n_pass = sum(votes)
        n_fail = n - n_pass

        # Margin: 0 = perfectly split, 1 = unanimous
        margin = abs(n_pass - n_fail) / n if n > 0 else 0.0
        passed = n_pass > n / 2
        confidence = margin

        # If confidence is too low and we have room to add more agents, signal caller
        if confidence < 0.6 and len(results) < 5:
            return None

        dims[dim] = {
            "passed": passed,
            "confidence": round(confidence, 4),
            "evidence": " | ".join(dict.fromkeys(clean_evidence(e) for e in evidences if e))[:240],  # cleaned, deduped, capped
            "weight": WEIGHTS[dim],
        }

    score = score_from_dims(dims)
    avg_conf = sum(v["confidence"] for v in dims.values()) / len(dims)

    return {
        "score": score,
        "confidence": round(avg_conf, 4),
        "dimensions": dims,
    }


def score_from_dims(dims: dict) -> int:
    """Compute weighted rubric score from aggregated dimension results."""
    return sum(WEIGHTS[d] for d, v in dims.items() if v.get("passed"))


# ---------------------------------------------------------------------------
# Deterministic black-box scoring (the canonical rubric)
# ---------------------------------------------------------------------------
# This is the SOURCE OF TRUTH for a score: given observed evidence (a recon
# bundle + two live probes), each of the 6 dimensions is a pure, deterministic
# pass/fail — no LLM judge. The same function scores BEFORE (raw target) and
# AFTER (through the generated proxy), so the delta is a real measured fact.


def score_dimensions(
    recon: dict,
    *,
    error_quality: tuple[bool, str] | None = None,
    idempotency: tuple[bool, str] | None = None,
) -> dict:
    """Compute the 6 deterministic dimensions from observed evidence.

    Args:
        recon: a `services.recon.probe_surface` bundle (has_openapi, has_mcp,
            has_docs, captcha, otp, token_auth, openapi, ...).
        error_quality: optional (passed, evidence) from `recon.probe_error_quality`.
            When omitted it is derived statically from the recon bundle
            (presence of an OpenAPI error schema) so the function stays pure.
        idempotency: optional (passed, evidence) from `recon.detect_idempotency`.
            When omitted it is derived from the recon bundle's spec.

    Returns the contract score shape:
        {"total": int, "dimensions": [{"dim", "passed", "evidence"}, ...]}
    Every dimension key in DIMENSIONS is always present.
    """
    recon = recon or {}
    openapi = recon.get("openapi") if isinstance(recon, dict) else None

    # --- api_surface: discoverable programmatic surface ---------------------
    if recon.get("has_openapi"):
        api_passed, api_ev = True, "OpenAPI/Swagger spec discoverable and parseable"
    elif recon.get("found", {}).get("/api"):
        api_passed, api_ev = True, "reachable /api surface (no spec)"
    else:
        api_passed, api_ev = False, "no OpenAPI spec and no discoverable /api surface"

    # --- auth: deterministic agent auth without human gate ------------------
    if recon.get("captcha"):
        auth_passed, auth_ev = False, "CAPTCHA detected on the auth surface — human-only"
    elif recon.get("otp"):
        auth_passed, auth_ev = False, "OTP / magic-link login detected — not agent-drivable"
    elif recon.get("token_auth") or recon.get("has_openapi"):
        auth_passed, auth_ev = (
            True,
            "token/key (or OpenAPI security) auth — deterministic for an agent",
        )
    else:
        auth_passed, auth_ev = False, "no token/key auth signal; auth path unproven"

    # --- error_quality: structured 4xx, not 200-with-error ------------------
    if error_quality is not None:
        err_passed, err_ev = bool(error_quality[0]), str(error_quality[1])
    else:
        # Static fallback: does the spec declare 4xx responses with schemas?
        err_passed, err_ev = _spec_declares_errors(openapi)

    # --- idempotency: safe-retry / no duplicate side effects ----------------
    if idempotency is not None:
        idem_passed, idem_ev = bool(idempotency[0]), str(idempotency[1])
    else:
        idem_passed, idem_ev = _static_idempotency(openapi)

    # --- mcp_availability: MCP endpoint discoverable ------------------------
    if recon.get("has_mcp"):
        mcp_passed, mcp_ev = True, "MCP endpoint / .well-known/mcp.json discoverable"
    else:
        mcp_passed, mcp_ev = False, "no MCP endpoint or manifest found"

    # --- docs: machine-readable agent docs ----------------------------------
    if recon.get("has_docs"):
        docs_passed, docs_ev = True, "machine-readable agent docs present (llms.txt / ai-plugin)"
    elif recon.get("has_openapi"):
        docs_passed, docs_ev = True, "OpenAPI doubles as machine-readable documentation"
    else:
        docs_passed, docs_ev = False, "no llms.txt / machine-readable docs"

    verdicts = {
        "api_surface": (api_passed, api_ev),
        "auth": (auth_passed, auth_ev),
        "error_quality": (err_passed, err_ev),
        "idempotency": (idem_passed, idem_ev),
        "mcp_availability": (mcp_passed, mcp_ev),
        "docs": (docs_passed, docs_ev),
    }

    dimensions = [
        {"dim": dim, "passed": bool(verdicts[dim][0]), "evidence": verdicts[dim][1]}
        for dim in DIMENSIONS
    ]
    total = sum(WEIGHTS[d["dim"]] for d in dimensions if d["passed"])
    return {"total": total, "dimensions": dimensions}


def _spec_declares_errors(openapi: dict | None) -> tuple[bool, str]:
    """Static error-quality check: does the spec declare 4xx responses w/ schema?"""
    if not isinstance(openapi, dict):
        return False, "no spec — error contract unverified"
    paths = openapi.get("paths") or {}
    if not isinstance(paths, dict):
        return False, "spec has no paths"
    for ops in paths.values():
        if not isinstance(ops, dict):
            continue
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            responses = op.get("responses") or {}
            if not isinstance(responses, dict):
                continue
            for code, resp in responses.items():
                if str(code).startswith("4") and isinstance(resp, dict):
                    if resp.get("content") or resp.get("schema"):
                        return True, f"spec declares structured 4xx responses (e.g. {code})"
    return False, "spec declares no structured 4xx error responses"


def _static_idempotency(openapi: dict | None) -> tuple[bool, str]:
    """Static idempotency check mirroring recon.detect_idempotency, spec-only."""
    if not isinstance(openapi, dict):
        return False, "no spec — cannot prove safe-retry"
    paths = openapi.get("paths") or {}
    if not isinstance(paths, dict):
        return False, "spec has no paths"
    has_idem_header = False
    has_put = False
    for ops in paths.values():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if str(method).lower() == "put":
                has_put = True
            if not isinstance(op, dict):
                continue
            for param in op.get("parameters", []) or []:
                if isinstance(param, dict) and "idempotency" in str(
                    param.get("name", "")
                ).lower():
                    has_idem_header = True
    if has_idem_header:
        return True, "explicit Idempotency-Key header parameter"
    if has_put:
        return True, "PUT routes present (idempotent by HTTP contract)"
    return False, "no idempotency mechanism advertised"


# ---------------------------------------------------------------------------
# Claude arbiter — resolves low-consensus dimensions
# ---------------------------------------------------------------------------

_ARBITER_SYSTEM = (
    "You are CATTS, a strict arbiter for agent-readiness audits. Multiple "
    "independent audit agents disagreed on whether a domain passes a specific "
    "readiness dimension. Adjudicate from the diverging evidence alone. Be "
    "skeptical: only pass the dimension when the evidence concretely supports "
    "it. Reply with ONLY a JSON object of the form "
    '{"passed": bool, "confidence": number between 0 and 1, "evidence": string}.'
)


async def arbitrate_dimension(dim: str, evidences: list[str]) -> dict:
    """Ask Claude to adjudicate a split dimension from the diverging evidence.

    Returns {"passed": bool, "confidence": float, "evidence": str}. Degrades to
    a neutral majority-vote-style fallback (confidence 0.5) when no keys are
    configured or the call fails.
    """
    if not key_pool.has_keys():
        return {
            "passed": False,
            "confidence": 0.5,
            "evidence": "no arbiter keys — majority fallback",
        }

    bullet_ev = "\n".join(f"- {e}" for e in evidences if e) or "- (no evidence provided)"
    prompt = (
        f"Dimension under dispute: {dim}\n\n"
        f"Diverging evidence from the audit agents:\n{bullet_ev}\n\n"
        "Decide whether this dimension PASSES. Return JSON only."
    )

    try:
        result = await claude_json(prompt, system=_ARBITER_SYSTEM, max_tokens=500)
    except Exception as exc:  # claude_json shouldn't raise, but be safe
        logger.debug("arbitrate_dimension(%s) failed: %s", dim, exc)
        result = {}

    if not isinstance(result, dict) or "passed" not in result:
        return {
            "passed": False,
            "confidence": 0.5,
            "evidence": "arbiter returned no verdict — majority fallback",
        }

    try:
        confidence = float(result.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))

    evidence = str(result.get("evidence", "")) or "arbiter verdict"
    return {
        "passed": bool(result.get("passed", False)),
        "confidence": round(confidence, 4),
        "evidence": evidence,
    }


async def catts_aggregate_with_arbiter(results: list[dict]) -> dict:
    """Aggregate agent results, resolving split dimensions via the Claude arbiter.

    Per dimension:
      - margin >= DELTA            -> accept the majority vote (fast path).
      - margin <  DELTA + keys     -> ask the arbiter to adjudicate.
      - margin <  DELTA + no keys  -> majority fallback (confidence = margin).

    Returns the same {score, confidence, dimensions} shape as catts_aggregate.
    Always returns a dict (never None) — the arbiter / majority fallback always
    yields a verdict for every dimension.
    """
    if not results:
        return {"score": 0, "confidence": 0.0, "dimensions": {}}

    arbiter_available = key_pool.has_keys()
    dims: dict[str, dict] = {}

    for dim in DIMENSIONS:
        votes: list[bool] = []
        evidences: list[str] = []
        for r in results:
            dim_data = r.get("dimensions", {}).get(dim, {})
            votes.append(bool(dim_data.get("passed", False)))
            ev = dim_data.get("evidence", "")
            if ev:
                evidences.append(ev)

        n = len(votes)
        n_pass = sum(votes)
        n_fail = n - n_pass
        margin = abs(n_pass - n_fail) / n if n > 0 else 0.0
        majority_passed = n_pass > n / 2
        dedup_ev = " | ".join(dict.fromkeys(e for e in evidences if e))

        if margin >= DELTA:
            # Clear consensus — accept the majority.
            dims[dim] = {
                "passed": majority_passed,
                "confidence": round(margin, 4),
                "evidence": dedup_ev,
                "weight": WEIGHTS[dim],
            }
        elif arbiter_available:
            # Split — let Claude adjudicate from the diverging evidence.
            verdict = await arbitrate_dimension(dim, evidences)
            dims[dim] = {
                "passed": bool(verdict.get("passed", majority_passed)),
                "confidence": round(float(verdict.get("confidence", 0.5)), 4),
                "evidence": verdict.get("evidence", dedup_ev) or dedup_ev,
                "weight": WEIGHTS[dim],
            }
        else:
            # Split and no arbiter — low-confidence majority fallback.
            dims[dim] = {
                "passed": majority_passed,
                "confidence": 0.5,
                "evidence": dedup_ev or "low-confidence majority",
                "weight": WEIGHTS[dim],
            }

    score = score_from_dims(dims)
    avg_conf = sum(v["confidence"] for v in dims.values()) / len(dims) if dims else 0.0
    return {
        "score": score,
        "confidence": round(avg_conf, 4),
        "dimensions": dims,
    }
