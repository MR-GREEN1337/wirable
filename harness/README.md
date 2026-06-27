# AgentReady Harness — skill library

These are the skill packages the OpenCode agents load inside Daytona sandboxes (image:
`./Dockerfile`). Each skill is a rigorous operator playbook: a one-line description,
when-to-use, methodology, tools, hard rules, and a STRICT output contract (exact JSON to an
exact path).

## Skills

| Skill        | Purpose                                                        | Output         |
|--------------|----------------------------------------------------------------|----------------|
| **audit**    | Drive a real browser + probe machine endpoints; score the 7 agent-readiness dimensions with literal evidence; stream screenshots. | `/output.json` |
| **fix**      | Read the cloned repo, map the real API, generate a working MCP server + `llms.txt` + OpenAPI + agent guide + evals. | `/fix-complete.json` |
| **outbound** | Write one peer-to-peer cold email to the founder using the audit as the lead magnet. | `/output.json` |
| **enrichment** | Find the founder's name/email/title for a domain, honest about confidence. | `/output.json` |
| **discovery** | Propose N SaaS companies likely to score poorly on agent-readiness — the best targets. | `/output.json` |

## Two locations, one source of truth

The backend loads three skills **by path** (it reads the file, substitutes `{domain}` /
`{repo}`, and uploads it as the agent task):

- `backend/.../audit_service.py` → **`harness/prompts/audit.md`**
- `backend/.../fix_service.py`   → **`harness/prompts/fix.md`**
- outbound generation            → **`harness/prompts/outbound.md`**

So `prompts/audit.md`, `prompts/fix.md`, `prompts/outbound.md` contain the **full** SOTA
content (these are canonical and must keep their filenames). `skills/<name>/SKILL.md`
mirrors that content for the library structure; `enrichment` and `discovery` live only
under `skills/`. When editing audit/fix/outbound, update BOTH the `prompts/*.md` file and
its `skills/*/SKILL.md` mirror so they don't drift.

## Screenshot streaming (audit skill)

The audit agent writes compressed JPEG frames + JSON sidecars as it drives the browser, so
the backend can stream the run live:

- `/screenshots/NNNN.jpg` — zero-padded 4-digit increasing sequence (`0001.jpg`, `0002.jpg`,
  …), JPEG quality ~60, viewport ~1280×800, target < 150 KB.
- `/screenshots/NNNN.json` — `{"caption": "...", "dimension": "<dim|general>", "url": "..."}`.

`/screenshots` is created world-writable in the Dockerfile, and the build smoke-tests that
a valid JPEG screenshot can be produced.
