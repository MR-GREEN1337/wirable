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

DIMENSIONS = [
    "discoverability",
    "auth",
    "mcp",
    "errors",
    "idempotency",
    "ratelimit",
    "docs",
]

WEIGHTS: dict[str, int] = {
    "discoverability": 15,
    "auth": 20,
    "mcp": 20,
    "errors": 15,
    "idempotency": 15,
    "ratelimit": 10,
    "docs": 5,
}

# Minimum margin (|agree - disagree| / total) to consider consensus reached
DELTA: float = 0.7


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
            "evidence": " | ".join(dict.fromkeys(e for e in evidences if e)),  # dedup + preserve order
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
