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
    "proxy_ready",
    "verify",
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
    def screenshot(seq: int, caption: str, dimension: str, image: str) -> dict:
        # image is a "data:image/jpeg;base64,..." data URL.
        return {
            "type": "screenshot",
            "seq": seq,
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
    def verify(before: int, after: int) -> dict:
        return {
            "type": "verify",
            "before": before,
            "after": after,
            "delta": after - before,
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
