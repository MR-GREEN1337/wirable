# MCP Server Generation — {repo} ({domain})

You are inside a cloned repository at **/repo**. This codebase powers the SaaS at
**{domain}**. An agent-readiness audit found it closed to autonomous agents. Your job:
make it agent-ready by generating a real, working MCP server plus the supporting
machine-readable surface — grounded in the *actual* API of this repo, not a template.

The output must read like an integration a senior engineer would ship: every tool maps to
a real endpoint, every description tells an agent exactly what to do, and the evals prove
an agent can complete the core workflow end to end.

---

## When to use

After an audit flags missing `mcp` / `discoverability` / `docs`. You have repo write
access and will open a PR. Generate, don't explore endlessly.

---

## Methodology

### 1. Map the real API surface (read, don't guess)

Identify the framework and route table. Look in the obvious places first:

- **FastAPI / Flask / Django**: `@app.<verb>`, `@router.<verb>`, `APIRouter`, `urls.py`,
  `views.py`. OpenAPI often at `/openapi.json` already.
- **Express / NestJS / Fastify**: `app.get/post/...`, `router.*`, `@Controller`/`@Get`,
  route files under `routes/`, `controllers/`, `api/`.
- **Rails**: `config/routes.rb`, `app/controllers/`.
- **Next.js**: `app/api/**/route.ts`, `pages/api/**`.

For each route record: **method, path, required auth, request schema (body/query/path
params), response schema, and what it semantically does.** Pull schemas from the actual
validators (Pydantic models, Zod schemas, DTOs, serializers) — not invented fields.

Find the **auth mechanism**: bearer token / API key header / OAuth / session cookie. The
MCP server must accept the credential the API actually requires (read env var name from the
code, e.g. `API_KEY`, `Authorization: Bearer`).

If an OpenAPI/Swagger spec already exists in the repo, read it — it's the ground truth for
schemas. If not, you will also emit one.

### 2. Decide which endpoints become tools

**EXPOSE** endpoints an autonomous agent calls to accomplish a user goal — the core
read/write resources (create/list/get/update/delete the product's primary objects,
search, the main "do the thing" action).

**SKIP**: health/ping/readiness, internal admin, webhook *receivers*, OAuth callbacks,
static asset routes, CSRF/login form posts, metrics. One tool per meaningful operation —
not one per HTTP route if several are trivial variants; collapse sensibly.

### 3. Generate the files

Create directories as needed. Use the MCP TypeScript SDK (`@modelcontextprotocol/sdk`),
stdio transport.

- **`/repo/mcp-server/index.ts`** — the server. Registers every tool, wires stdio
  transport, reads the auth credential from an env var, makes real HTTP calls to
  `{domain}`'s API, and returns structured results. Include graceful error mapping
  (surface the upstream status + body to the agent).
- **`/repo/mcp-server/tools/<name>.ts`** — one file per exposed resource/operation. Each
  exports the tool name, an input schema (Zod), the rich description (rules below), and the
  handler that calls the API.
- **`/repo/mcp-server/package.json`** — deps (`@modelcontextprotocol/sdk`, `zod`), a
  `build`/`start` script, `"type": "module"`.
- **`/repo/llms.txt`** — per llmstxt.org: `# {product}`, a one-line summary, a `> blockquote`,
  then sections (`## API`, `## Authentication`, `## MCP`) with links and the one-sentence
  capability list. Point to the MCP server and the agent guide.
- **`/repo/openapi.json`** — if the repo lacks one, emit a minimal valid OpenAPI 3.1 spec
  covering the exposed endpoints (so `discoverability` + `docs` pass too).
- **`/repo/docs/agent-guide.md`** — for an AI agent: what this product is, how to
  authenticate (which env var / token), the available tools, and a worked example of the
  core workflow (the exact sequence of tool calls to accomplish the primary job).
- **`/repo/evals/basic.ts`** — 3–5 eval cases that PROVE an agent can complete the core
  workflow against the generated tools (e.g. authenticate → create the primary resource →
  read it back → assert it exists → clean up). Each case: a name, the steps, and an
  assertion. They should be runnable (`tsx evals/basic.ts`) and fail loudly if a tool is
  miswired.

### 4. Tool description rules (this is what makes it SOTA)

Every tool description MUST answer all four:

1. **WHAT** it does — one precise sentence.
2. **WHEN** to call it — the user-intent trigger.
3. **WHAT it returns** — the shape of the success payload.
4. **WHAT can fail** — the error types + status codes.

> Bad: `create_page` — "Creates a page."
>
> Good: "Creates a new page in the specified workspace. **Call when** the user asks to
> create, write, draft, or add a document. **Returns** `{id, url, createdAt}`. **Fails**
> with `401` if the token is missing/invalid, `403` if it lacks `write` scope, `404` if
> the workspace id doesn't exist, `422` if `title` is empty."

Input schemas must use the **real** field names and types from the codebase, with
per-field `.describe()` so the agent knows what each param means.

---

## Hard limits

- Read what you need to map the API; don't read the whole repo. Prefer the routes/schemas.
- **20-minute hard cap.** Write files, then stop — do not keep exploring after writing.
- Tools must call the **real** API paths you found, with the **real** auth mechanism. No
  invented endpoints, no fabricated fields. If a resource's schema is unclear, expose the
  fields you can verify and note the uncertainty in the description.
- Don't expose destructive admin operations or anything behind a non-API auth wall.

---

## OUTPUT CONTRACT — when done, write `/fix-complete.json` then STOP

```json
{
  "status": "done",
  "tools_generated": 7,
  "files": [
    "mcp-server/index.ts",
    "mcp-server/package.json",
    "mcp-server/tools/create_item.ts",
    "mcp-server/tools/list_items.ts",
    "mcp-server/tools/get_item.ts",
    "llms.txt",
    "openapi.json",
    "docs/agent-guide.md",
    "evals/basic.ts"
  ]
}
```

`tools_generated` = number of tool files under `mcp-server/tools/`. `files` = every path
you wrote, relative to `/repo`. Then stop.
