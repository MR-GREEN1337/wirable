"""Seed the public registry with REAL audits + hosted MCPs.

Runs the genuine harness (no fake scores). For each URL:
  1. create/reuse Company(domain) + Audit
  2. orchestrator.run_workflow  -> real aggregated score
  3. persist_test_result        -> company.score
  4. generate_proxy_config      -> real MCP tools (black-box discovery)
  5. persist_proxy_config       -> pr_status="hosted" (shows in /registry)

Usage (inside the backend container, where Daytona + Claude keys live):
    python -m scripts.seed_registry https://stripe.com https://resend.com ...

Bypasses ONLY the billing/Pro gate (correctness is unchanged). Idempotent per
domain: re-running re-audits the same Company and overwrites its hosted proxy.
"""
import asyncio
import sys
import uuid

import httpx

from src.core.database import AsyncSessionLocal
from src.models.audit import Audit
from src.models.company import Company
from src.services import orchestrator, proxy_generator, proxy_runtime, test_service
from sqlalchemy import select

_VERBS = ("get", "post", "put", "patch", "delete")


def _domain(url: str) -> str:
    d = url.strip().lower()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p):]
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


async def _fetch_spec(spec_url: str) -> dict | None:
    """Fetch + parse an OpenAPI spec directly (JSON or YAML). Big specs OK."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(spec_url)
            if r.status_code >= 400:
                return None
            try:
                return r.json()
            except Exception:
                import yaml  # PyYAML; degrade to None if absent
                return yaml.safe_load(r.text)
    except Exception:
        return None


def _endpoints_from_spec(spec: dict) -> tuple[list[dict], str]:
    """OpenAPI -> [{method, path, summary, params}] + upstream base from servers[]."""
    paths = spec.get("paths") or {}
    base = ""
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        base = str(servers[0].get("url") or "")
    eps: list[dict] = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in _VERBS or not isinstance(op, dict):
                continue
            names = [p.get("name") for p in (op.get("parameters") or [])
                     if isinstance(p, dict) and p.get("name")]
            try:
                schema = (op.get("requestBody", {}).get("content", {})
                          .get("application/json", {}).get("schema", {}))
                names += list((schema.get("properties") or {}).keys())
            except Exception:
                pass
            eps.append({
                "method": method,
                "path": path,
                "summary": str(op.get("summary") or op.get("operationId") or "").strip(),
                "params": names,
            })
    return eps, base


async def seed_one(url: str, spec_url: str = "", api_base: str = "") -> dict:
    domain = _domain(url)
    run_id = str(uuid.uuid4())
    out = {"url": url, "domain": domain, "run_id": run_id}

    # 1. anchor rows
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Company).where(Company.domain == domain))
        company = res.scalar_one_or_none()
        if not company:
            company = Company(domain=domain)
            db.add(company)
            await db.commit()
            await db.refresh(company)
        audit = Audit(company_id=company.id)
        db.add(audit)
        await db.commit()
        await db.refresh(audit)
        company_id, audit_id = company.id, audit.id

    # 2. REAL audit
    print(f"[{domain}] auditing… (run {run_id})", flush=True)
    agg = await orchestrator.run_workflow(run_id, url, access=None)
    if not agg:
        out["error"] = "no aggregate (audit produced nothing)"
        return out
    out["score"] = agg.get("score")

    # 3. persist score
    async with AsyncSessionLocal() as db:
        await test_service.persist_test_result(db, audit_id, company_id, agg)

    # 4. build the MCP — grounded in the REAL OpenAPI spec when one is given
    #    (rich named tools), else black-box discovery from the audit surface.
    code_eps, code_base = [], ""
    if spec_url:
        spec = await _fetch_spec(spec_url)
        if isinstance(spec, dict):
            code_eps, derived_base = _endpoints_from_spec(spec)
            code_base = api_base or derived_base
            out["spec_endpoints"] = len(code_eps)
    print(f"[{domain}] score={out['score']} — generating MCP "
          f"({'spec:'+str(len(code_eps))+' eps' if code_eps else 'black-box'})…", flush=True)
    gen_kwargs = {"target_id": run_id}
    if code_eps:
        gen_kwargs["code_endpoints"] = code_eps
        gen_kwargs["code_base_url"] = code_base
    config = await proxy_generator.generate_proxy_config(run_id, None, **gen_kwargs)
    out["tools"] = [t.name for t in (config.tools or [])]
    if not config.tools:
        out["error"] = "no tools generated — not hosting (would be an empty MCP)"
        return out

    # 5. host it
    mcp_url = proxy_runtime.mcp_url_for(run_id)
    await proxy_runtime.persist_proxy_config(
        run_id, config, mcp_url=mcp_url, audit_id=audit_id, company_id=company_id
    )
    out["mcp_url"] = mcp_url
    out["hosted"] = True
    print(f"[{domain}] HOSTED {len(config.tools)} tools -> {mcp_url}", flush=True)
    return out


async def main(entries: list[str]) -> None:
    results = []
    for raw in entries:
        # entry = "url" or "url|spec_url" or "url|spec_url|api_base"
        parts = [p.strip() for p in raw.split("|")]
        url = parts[0]
        spec_url = parts[1] if len(parts) > 1 else ""
        api_base = parts[2] if len(parts) > 2 else ""
        try:
            results.append(await seed_one(url, spec_url, api_base))
        except Exception as e:  # never let one product abort the batch
            results.append({"url": url, "domain": _domain(url), "error": f"{type(e).__name__}: {e}"})
            print(f"[{_domain(url)}] FAILED: {e}", flush=True)
    print("\n===== SEED SUMMARY =====", flush=True)
    for r in results:
        if r.get("hosted"):
            print(f"  ✅ {r['domain']:20} score={r.get('score')}  tools={len(r.get('tools',[]))}", flush=True)
        else:
            print(f"  ❌ {r['domain']:20} {r.get('error','?')}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("usage: python -m scripts.seed_registry '<url>[|spec_url[|api_base]]' ...")
        sys.exit(1)
    asyncio.run(main(args))
