"""
Wirable canonical contracts — the single source of truth shared by the
orchestrator (which EMITS these events), the endpoints (which expose them), and
Wave 2 (which must align to them).

Three things live here:
  1. SSE run-event constructors + the literal type/enum vocabulary the frontend
     renders. Use the `events` helpers so producers never hand-roll a malformed
     event shape.
  2. The 6 deterministic score dimensions with weights summing to 100.
  3. The proxy_config schema (ProxyConfig / ProxyTool) that the generator
     (Wave 2) produces and the ProxyRuntime (Wave 2) serves.

Keep this file dependency-light (stdlib + pydantic) so anything can import it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# ---------------------------------------------------------------------------
# (a) SSE run-event vocabulary
# ---------------------------------------------------------------------------
# The orchestrator drives a deterministic workflow and emits these events onto
# the in-process SSE bus (see services/test_service.py). The frontend run page
# renders each `type`. A run always terminates with exactly one of {done, error}.

Phase = Literal["recon", "test", "score", "generate", "deploy", "verify"]
PhaseStatus = Literal["start", "done"]
TargetKind = Literal["api", "site"]
Workflow = Literal["signup", "core_action", "error_handling", "retry_idempotency"]

# Every event type the bus can carry (used for validation / docs).
EVENT_TYPES: tuple[str, ...] = (
    "phase",
    "classify",
    "line",
    "screenshot",
    "tool_call",
    "workflow_result",
    "score",
    "proxy_tool",
    "proxy_ready",
    "fix_pr",
    "verify",
    "needs_input",
    "done",
    "error",
)


class events:
    """Typed constructors for every SSE run-event.

    Each returns a plain dict ready to hand to `test_service.emit`. Centralising
    the shapes here keeps producer and consumer in lockstep.
    """

    @staticmethod
    def phase(phase: Phase, status: PhaseStatus) -> dict:
        return {"type": "phase", "phase": phase, "status": status}

    @staticmethod
    def classify(kind: TargetKind, evidence: str) -> dict:
        return {"type": "classify", "kind": kind, "evidence": evidence}

    @staticmethod
    def line(ok: bool, msg: str) -> dict:
        return {"type": "line", "ok": ok, "msg": msg}

    @staticmethod
    def screenshot(
        seq: int, caption: str, dimension: str, image: str, agent: int = 0
    ) -> dict:
        # image is a "data:image/jpeg;base64,..." data URL.
        # `agent` (0..N-1) tags which parallel CATTS agent produced the frame so
        # the frontend can render one live tile per agent. Defaults to 0 for
        # back-compat with any single-agent producer.
        return {
            "type": "screenshot",
            "seq": seq,
            "agent": agent,
            "caption": caption,
            "dimension": dimension,
            "image": image,
        }

    @staticmethod
    def tool_call(
        name: str,
        request: Any,
        response: Any,
        *,
        success: bool,
        error_code: Optional[str] = None,
        retryable: bool = False,
    ) -> dict:
        return {
            "type": "tool_call",
            "name": name,
            "request": request,
            "response": response,
            "normalized": {
                "success": success,
                "error_code": error_code,
                "retryable": retryable,
            },
        }

    @staticmethod
    def workflow_result(workflow: Workflow, passed: bool, evidence: str) -> dict:
        return {
            "type": "workflow_result",
            "workflow": workflow,
            "passed": passed,
            "evidence": evidence,
        }

    @staticmethod
    def score(total: int, dimensions: list[dict]) -> dict:
        # dimensions: [{"dim": str, "passed": bool, "evidence": str}, ...]
        return {"type": "score", "total": total, "dimensions": dimensions}

    @staticmethod
    def proxy_tool(
        name: str,
        *,
        method: Optional[str] = None,
        path: Optional[str] = None,
        kind: str = "http",
    ) -> dict:
        # Emitted once per tool as the generator maps it, so the build stream can
        # materialize the proxy's tool list one row at a time as it's built.
        #   method/path describe the upstream operation (e.g. POST /v1/customers);
        #   kind is the action type ("http" | "playwright").
        return {
            "type": "proxy_tool",
            "name": name,
            "method": method,
            "path": path,
            "kind": kind,
        }

    @staticmethod
    def proxy_ready(mcp_url: str, tools: list[dict], advertise: dict) -> dict:
        # tools: [{"name": str, "description": str}, ...]
        # advertise: {"well_known": dict, "llms_txt": str, "link_tag": str, "header": str}
        return {
            "type": "proxy_ready",
            "mcp_url": mcp_url,
            "tools": tools,
            "advertise": advertise,
        }

    @staticmethod
    def fix_pr(
        pr_url: str,
        files: list[str],
        *,
        branch: Optional[str] = None,
        repo: Optional[str] = None,
        diff: Optional[str] = None,
        error: Optional[str] = None,
    ) -> dict:
        # Emitted after the FIX flow opens a PR on the user's connected repo.
        # On failure, pr_url is "" and `error` carries the reason.
        # `diff` is the unified diff of the agent-ready changes (capped); omitted
        # when unavailable (e.g. the REST file-drop fallback) so the UI degrades.
        return {
            "type": "fix_pr",
            "pr_url": pr_url,
            "files": files,
            "branch": branch,
            "repo": repo,
            "diff": diff,
            "error": error,
        }

    @staticmethod
    def verify(before: int, after: int) -> dict:
        return {
            "type": "verify",
            "before": before,
            "after": after,
            "delta": after - before,
        }

    @staticmethod
    def needs_input(prompt: str, kind: str, request_id: str) -> dict:
        # Emitted when the in-sandbox agent is blocked and asks the human for a
        # value (an OTP, a credential, or free text). The frontend renders an
        # input affordance and POSTs the answer to /run/{run_id}/input, which the
        # camera loop relays into the sandbox so the agent resumes.
        #   kind: "otp" | "credential" | "text"
        return {
            "type": "needs_input",
            "prompt": prompt,
            "kind": kind,
            "request_id": request_id,
        }

    @staticmethod
    def done() -> dict:
        return {"type": "done"}

    @staticmethod
    def error(msg: str) -> dict:
        return {"type": "error", "msg": msg}


# ---------------------------------------------------------------------------
# (b) The 6 deterministic score dimensions (weights sum to 100)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dimension:
    key: str
    weight: int
    description: str


SCORE_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("api_surface", 20, "Is there a programmatic surface an agent can drive (OpenAPI / typed endpoints)?"),
    Dimension("auth", 20, "Can an agent authenticate deterministically (tokens/keys, not a human-only login)?"),
    Dimension("error_quality", 15, "Are errors machine-readable: stable codes, retryable signals, actionable messages?"),
    Dimension("idempotency", 15, "Can an action be safely retried without duplicate side effects?"),
    Dimension("mcp_availability", 20, "Is an MCP endpoint already discoverable/served?"),
    Dimension("docs", 10, "Is there agent-facing documentation (llms.txt / machine docs)?"),
)

# Convenience views.
DIMENSION_KEYS: tuple[str, ...] = tuple(d.key for d in SCORE_DIMENSIONS)
DIMENSION_WEIGHTS: dict[str, int] = {d.key: d.weight for d in SCORE_DIMENSIONS}

assert sum(DIMENSION_WEIGHTS.values()) == 100, "score dimension weights must sum to 100"


# ---------------------------------------------------------------------------
# (c) proxy_config schema
# ---------------------------------------------------------------------------
# The generator (Wave 2) produces a ProxyConfig; the ProxyRuntime (Wave 2)
# serves it as MCP-over-HTTP. Kept as dataclasses so it round-trips to/from
# plain dicts (JSON columns) without a pydantic dependency at the call site.


@dataclass
class ProxyTool:
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    # action.type is "http" or "playwright"; remaining keys are the mapping
    # (method/url/template for http; selectors/steps for playwright).
    action: dict = field(default_factory=dict)
    # error_rules: maps observed upstream signals -> normalized error semantics.
    error_rules: dict = field(default_factory=dict)
    # idempotency.key_fields: input fields that form the idempotency key.
    idempotency: dict = field(default_factory=lambda: {"key_fields": []})


@dataclass
class ProxyConfig:
    target_id: str
    kind: TargetKind  # "api" | "site"
    base_url: str
    auth_ref: Optional[str] = None  # opaque reference to a stored credential (auth broker, Wave 2)
    tools: list[ProxyTool] = field(default_factory=list)
    # advertise: discovery bundle the proxy publishes so agents can find it.
    #   {"well_known": dict, "llms_txt": str, "link_tag": str, "header": str}
    advertise: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "kind": self.kind,
            "base_url": self.base_url,
            "auth_ref": self.auth_ref,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "action": t.action,
                    "error_rules": t.error_rules,
                    "idempotency": t.idempotency,
                }
                for t in self.tools
            ],
            "advertise": self.advertise,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProxyConfig":
        return cls(
            target_id=d["target_id"],
            kind=d["kind"],
            base_url=d["base_url"],
            auth_ref=d.get("auth_ref"),
            tools=[
                ProxyTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("input_schema", {}),
                    action=t.get("action", {}),
                    error_rules=t.get("error_rules", {}),
                    idempotency=t.get("idempotency", {"key_fields": []}),
                )
                for t in d.get("tools", [])
            ],
            advertise=d.get("advertise", {}),
        )
