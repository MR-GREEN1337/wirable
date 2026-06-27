# SOURCES — techniques borrowed into the agent-readiness audit skill

The `audit` skill (`harness/prompts/audit.md` ≡ `harness/skills/audit/SKILL.md`) was
hardened by borrowing concrete methodology from the canonical open-source web-agent QA and
agent-eval work. Each technique below is traceable to its source.

| Borrowed technique | Where it landed in the skill | Source |
|---|---|---|
| **Task = bounded task + concrete success predicate** (don't match a trajectory; check the final state). Modeled the 4 canonical workflows (signup, core_action, error_handling, retry_idempotency) as discrete tasks each with a pass predicate. | "The 4 canonical workflows" table + each WORKFLOW section. | WebArena — https://webarena.dev/ ; paper https://arxiv.org/abs/2307.13854 |
| **Outcome-based grading, not HTTP 200 / not the path** — success is a verified state change or a verified parseable result; "200 with an error in HTML" is a FAIL. Backend-state verification for state-changing tasks; type/normalization-aware comparison over substring matching. | "Grade the outcome, not the path" principle; the *Outcome rule* on api_surface / mcp_availability / error_quality; core_action read-back verification. | WebArena Verified — https://openreview.net/forum?id=94tlGxmqkN |
| **Independent Validator second pass** — a separate check re-confirms the actor's claimed success/failure, because a single actor reporting its own success yields false positives (e.g. "clicked add-to-cart" but nothing was added). Pushed Skyvern from ~68.7%→85.85% on WebVoyager. | The "VALIDATE — the second-opinion pass" section (run before writing /output.json). | Skyvern 2.0 — https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/ ; repo https://github.com/Skyvern-AI/skyvern |
| **`page.validate("...")`-style natural-language assertion of state after an action.** | core_action: "verify it actually succeeded — not merely returned 200" via a read-back / confirmation-element assertion. | Skyvern docs — https://www.skyvern.com/docs/developers/getting-started/core-concepts |
| **Screenshot-trajectory + judge evaluation; the frames are the forensic record judged after the run.** Reinforced the existing screenshot contract as evidence the verdict is checked against. | SCREENSHOT CONTRACT framed as the record the VALIDATE pass re-examines. | WebVoyager — https://arxiv.org/abs/2401.13919 ; repo https://github.com/MinorJerry/WebVoyager |
| **Step success = element correct AND operation correct** (a partial match is not success); each step judged independently against its predicate. | error_quality requires BOTH correct 4xx status class AND a machine-parseable body; idempotency requires the duplicate-check, not just a 2xx. | Mind2Web — https://arxiv.org/abs/2306.06070 ; site https://osu-nlp-group.github.io/Mind2Web/ |
| **Accessibility-tree-first observation** (role+name per interactive element) over vision-only — cheaper, more precise, far less flaky for finding CAPTCHA iframes, OTP inputs, API-key fields. | Environment note: "Observe via the accessibility tree first" (`page.accessibility.snapshot()` / `get_by_role`); used in auth + api_surface nav detection. | browser-use — https://github.com/browser-use/browser-use ; Playwright MCP — https://github.com/microsoft/playwright-mcp |
| **Eval-design discipline:** grade outcome over trajectory; multi-dimensional rubric scored per-dimension; give the judge an "Unknown/lower-confidence out"; calibrate confidence to certainty; clean isolated state to cut flakiness. | The VALIDATE pass's 4 checks; "prefer an honest lower confidence over a confident guess"; per-dimension scoring; non-destructive test payloads. | Anthropic, *Demystifying evals for AI agents* — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents |
| **MCP liveness = JSON-RPC handshake** (`initialize` then `tools/list`), or SSE `text/event-stream` + endpoint event — a path existing is not enough; the server must respond. | mcp_availability: the curl `initialize`/`tools/list` handshake and the SSE check; "a 200 that is HTML or fails JSON-RPC is a FAIL". | MCP spec (tools) — https://modelcontextprotocol.io/specification/2025-11-25/server/tools ; MCP health check — https://www.openstatus.dev/play/mcp-health |
| **Deterministic agent-readiness audits** (WebMCP tools, accessibility-tree integrity, llms.txt presence at domain root; 404 ⇒ no llms.txt). | api_surface (WebMCP/openapi probes), mcp_availability (WebMCP), docs (llms.txt presence + validity per spec). | Chrome Lighthouse Agentic Browsing — https://developer.chrome.com/docs/lighthouse/agentic-browsing/scoring ; llms.txt audit — https://developer.chrome.com/docs/lighthouse/agentic-browsing/llms-txt |
| **llms.txt validity definition** (Markdown at root: title + summary + link sections). | docs dimension PASS condition. | llms.txt spec — https://llmstxt.org/ |

## Contract note (unchanged on purpose)

The hardening kept the backend contract intact and, in doing so, **fixed a drift bug**: the
backend parser (`backend/src/agents/catts.py` → `DIMENSIONS = DIMENSION_KEYS` from
`backend/src/core/contracts.py`) reads exactly **6** dimension keys —
`api_surface, auth, error_quality, idempotency, mcp_availability, docs`. The previous skill
emitted a different 7-key set (`discoverability, mcp, errors, ratelimit, …`), which the
backend silently overwrote with "not evaluated" failures. The skill now emits the 6
canonical keys. The `/output.json` schema (`{domain, dimensions:{<key>:{passed, confidence,
evidence}}, summary}`) and the `/screenshots/NNNN.jpg` + `NNNN.json` sidecar convention are
unchanged.
