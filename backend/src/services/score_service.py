"""
Score service — static analysis helpers for projecting post-fix scores.
"""
from typing import Callable

from ..agents.catts import WEIGHTS


def compute_score(dims: dict) -> int:
    """Compute weighted rubric score from aggregated dimension results."""
    return sum(WEIGHTS[d] for d, v in dims.items() if v.get("passed"))


def project_after(files: dict[str, str], before_dims: dict) -> dict:
    """
    Static analysis of generated MCP artifacts.

    Looks at file paths and content to determine which previously-failing
    dimensions would now pass after merging the PR.

    Args:
        files: mapping of relative path → file content produced by the fix agent.
        before_dims: the CATTS-aggregated dimension dict from the pre-fix audit.

    Returns:
        {
          "score": int,            # projected total score after merge
          "gained": int,           # points gained
          "verified_dims": list,   # dims we can statically confirm are fixed
          "unverified_dims": list, # dims that need live testing to confirm
        }
    """
    # Static checks — these can be inferred from artifact presence alone
    checks: dict[str, "Callable[[dict[str, str]], bool]"] = {
        "discoverability": lambda f: (
            "llms.txt" in f
            and any("openapi" in k.lower() or "schema" in k.lower() for k in f)
        ),
        "mcp": lambda f: (
            any("mcp-server" in k or "mcp_server" in k for k in f)
            and any(k.endswith(".ts") or k.endswith(".py") or k.endswith(".json") for k in f)
        ),
        "errors": lambda f: any(
            "tools" in k or "handlers" in k for k in f
        ),
        "docs": lambda f: any(
            "agent-guide" in k or "agent_guide" in k or "llms.txt" in k for k in f
        ),
    }

    # These require a live sandbox run to verify
    needs_live = ["auth", "idempotency", "ratelimit"]

    gained = 0
    verified: list[str] = []
    unverified: list[str] = []

    for dim, check in checks.items():
        was_failing = not before_dims.get(dim, {}).get("passed", False)
        if was_failing and check(files):
            gained += WEIGHTS[dim]
            verified.append(dim)

    for dim in needs_live:
        if not before_dims.get(dim, {}).get("passed", False):
            unverified.append(dim)

    before_score = compute_score(before_dims)

    return {
        "score": before_score + gained,
        "gained": gained,
        "verified_dims": verified,
        "unverified_dims": unverified,
    }
