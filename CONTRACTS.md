# Wirable — Canonical Contracts (Wave 1)

Wirable is a black-box product that (1) **tests** whether an AI agent can complete
real workflows on any platform and **scores** it, then (2) generates and hosts an
**MCP proxy** in front of that platform to fix the semantic breakage.

This document is the handoff contract. Wave 2 (real ProxyRuntime, generator,
auth broker, discovery bundle, real verify, frontend run page) MUST align to it.
The machine-readable source of truth is `backend/src/core/contracts.py`.

---

## Run workflow

```
recon ──▶ classify(api | site) ──▶ test(branch) ──▶ score ──▶ [GATE: configure auth]
                                                                      │
                                              POST /run/{id}/proxy ───┤
                                                                      ▼
                                            generate ──▶ deploy ──▶ verify
```

- `recon` → `classify` → `test` → `score` run automatically on `POST /run` and
  the stream **STOPS after `score`**.
- The proxy steps (`generate` → `deploy` → `verify`) are **gated** behind
  `POST /run/{id}/proxy`, which is where the owner supplies auth. They emit on
  the **same SSE bus** (keyed by `run_id`), so the frontend keeps streaming
  `GET /run/{id}/stream`.

Driver: `backend/src/services/orchestrator.py` → `run_workflow(run_id, url)`.

---

## (a) SSE run-events

Every event is a JSON object with a `type`. A run always terminates with exactly
one of `done` / `error`. Use the `events.*` constructors in `core/contracts.py`.

```jsonc
{"type":"phase","phase":"recon|test|score|generate|deploy|verify","status":"start|done"}
{"type":"classify","kind":"api|site","evidence":"<string>"}
{"type":"line","ok":true,"msg":"<string>"}
{"type":"screenshot","seq":3,"caption":"<string>","dimension":"<string>","image":"data:image/jpeg;base64,..."}
{"type":"tool_call","name":"<string>","request":{...},"response":{...},
  "normalized":{"success":true,"error_code":null,"retryable":false}}
{"type":"workflow_result","workflow":"signup|core_action|error_handling|retry_idempotency","passed":true,"evidence":"<string>"}
{"type":"score","total":72,"dimensions":[{"dim":"<string>","passed":true,"evidence":"<string>"}]}
{"type":"proxy_ready","mcp_url":"<string>",
  "tools":[{"name":"<string>","description":"<string>"}],
  "advertise":{"well_known":{...},"llms_txt":"<string>","link_tag":"<string>","header":"<string>"}}
{"type":"verify","before":40,"after":85,"delta":45}
{"type":"done"}
{"type":"error","msg":"<string>"}
```

Note: `screenshot` also carries a `dimension` (the field name in the contract is
`dimension`; the dimension data shape mirrors the live screenshot streaming).

---

## (b) Score dimensions (6, weights sum to 100)

Deterministic, defined in `core/contracts.SCORE_DIMENSIONS` and realigned in
`agents/catts.py`:

| key                | weight | meaning |
|--------------------|:------:|---------|
| `api_surface`      | 20 | Programmatic surface an agent can drive (OpenAPI / typed endpoints) |
| `auth`             | 20 | Deterministic agent auth (tokens/keys, not human-only login) |
| `error_quality`    | 15 | Machine-readable errors: stable codes, retryable signals, actionable |
| `idempotency`      | 15 | Actions safely retryable without duplicate side effects |
| `mcp_availability` | 20 | MCP endpoint already discoverable / served |
| `docs`             | 10 | Agent-facing docs (llms.txt / machine docs) |

`score.total` = sum of weights of passed dimensions (0–100).

---

## (c) proxy_config schema

`core/contracts.ProxyConfig` / `ProxyTool` (dataclasses with `to_dict` /
`from_dict`). Produced by the generator (Wave 2), served by the ProxyRuntime
(Wave 2).

```
ProxyConfig {
  target_id: str
  kind: "api" | "site"
  base_url: str
  auth_ref: str | null            # opaque ref to a stored credential (auth broker, Wave 2)
  tools: [ ProxyTool {
    name: str
    description: str
    input_schema: dict            # JSON schema for tool args
    action: {                     # how the proxy fulfills the tool
      type: "http" | "playwright"
      ...mapping                  # http: method/url/template; playwright: selectors/steps
    }
    error_rules: dict             # observed upstream signal -> normalized error semantics
    idempotency: { key_fields: [str] }
  } ]
  advertise: {                    # discovery bundle the proxy publishes
    well_known: dict              # /.well-known/mcp.json body
    llms_txt: str
    link_tag: str                 # <link rel="mcp" ...>
    header: str                   # Link: <...>; rel="mcp"
  }
}
```

---

## (d) Canonical endpoints

| Method | Path | Purpose | Status |
|--------|------|---------|--------|
| POST | `/api/v1/run` | `{url}` → `{run_id}` — start a test run | ✅ Wave 1 |
| GET  | `/api/v1/run/{run_id}/stream` | SSE of run-events | ✅ Wave 1 |
| POST | `/api/v1/run/{run_id}/proxy` | `{auth:{...}}` → `{mcp_url}` — configure auth + generate + deploy + verify | ✅ Wave 1 (stubbed generate/deploy/verify) |
| GET  | `/api/v1/proxy/{id}/mcp` | ProxyRuntime (MCP over HTTP) | 🔜 Wave 2 (501) |
| GET  | `/api/v1/proxy/{id}/.well-known/mcp.json` | Discovery manifest | ✅ serves `advertise.well_known` if present (else 404) |
| POST | `/api/v1/proxy/{id}/keys` | Owner issues scoped agent keys | 🔜 Wave 2 (stub key) |
| GET  | `/api/v1/dashboard` | List of targets w/ score + proxy status | ✅ Wave 1 |

Other surviving endpoints: `/api/v1/auth/*` (Google + guest), `/api/v1/track/*`
(open/click pixels), `/health`.

---

## (e) Pages

| Route | Purpose |
|-------|---------|
| `/` | Landing + run input (HeroAudit kicks off `POST /run`, streams, links to `/run/[id]`) |
| `/run/[id]` | Live run page: phases, classify, tool calls, score, proxy gate + verify (🔜 Wave 2 rebuild) |
| `/dashboard` | Targets list (score + proxy status) |
| `/signin` | Google + guest auth |

---

## Wave 2 TODOs (seams left in code, marked `# TODO(Wave2):`)

- `services/proxy_generator.py` — real ProxyConfig generation from recon + test evidence.
- `services/proxy_runtime.py` — real MCP-over-HTTP runtime, http/playwright dispatch, error/idempotency enforcement, auth broker, scoped agent keys.
- `endpoints/proxy.py` — real deploy + real before/after verify (re-test through the proxy); persist ProxyConfig (reuse the `MCP` model JSON columns) instead of the in-process `_ADVERTISE_CACHE`.
- `endpoints/dashboard.py` — return the hosted `mcp_url` once proxies are persisted.
- Build the `/run/[id]` frontend page that renders the full run-event stream + the proxy gate.

## Notes on reused tables (no migration this wave)

- `Audit` table → a **test run** (per `run_id`). `AuditStep` → per-dimension result.
- `MCP` table → the generated **proxy** (proxy_config + hosted endpoint). JSON
  columns (`schemas_json`, `llms_txt`, etc.) will hold the `ProxyConfig`.
- `Company` → the **target**; `Client` → the owner's link to a target.
