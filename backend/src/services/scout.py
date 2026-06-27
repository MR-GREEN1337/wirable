"""
Scout — the autonomous agency loop orchestrator.

One scout cycle, end to end:

    1. DISCOVER  — propose real SaaS targets in a category that likely score
                   poorly on agent-readiness; create a Company row per new domain
                   (source="scout", outbound_status="discovered").
    2. AUDIT     — for each target, run the full N=3 CATTS audit pipeline and
                   persist it (reusing audit_service.persist_audit_result so it
                   never diverges from the /audit endpoint).
    3. ENRICH    — best-effort find the founder name/email/title; honest
                   confidence. No email → leave the company at "audited".
    4. CONTACT   — if a founder email was found, send the cold audit email with
                   the worst failing dimension as the hook + a tracking pixel,
                   persist the OutboundEmail row, set outbound_status="contacted".

Every step is defensive: one target failing must never abort the batch, and a
scout failure must never crash the app (the background loop catches everything).
"""
from __future__ import annotations

import uuid

from loguru import logger

from ..core.database import AsyncSessionLocal
from ..models.audit import Audit
from ..models.company import Company
from . import audit_service
from .discovery_service import discover_targets
from .enrichment_service import enrich_founder
from .outbound_service import send_audit_email_for_company


async def _process_target(target: dict) -> dict:
    """Run audit → enrich → (maybe) contact for one discovered company.

    Opens its OWN session so a failure on one target is fully isolated. Returns
    a per-target summary ``{domain, score, status}``. Never raises.
    """
    domain = target.get("domain", "")
    summary = {"domain": domain, "score": None, "status": "discovered", "contacted": False}

    # --- create the Company row -------------------------------------------
    company_id: uuid.UUID
    audit_id: uuid.UUID
    try:
        async with AsyncSessionLocal() as db:
            company = Company(
                id=uuid.uuid4(),
                domain=domain,
                name=target.get("name"),
                source="scout",
                discovery_reason=target.get("reason"),
                outbound_status="auditing",
            )
            db.add(company)
            audit = Audit(id=uuid.uuid4(), company_id=company.id, n_agents=3)
            db.add(audit)
            await db.commit()
            company_id, audit_id = company.id, audit.id
    except Exception as exc:
        logger.warning(f"[scout] failed to create company for {domain}: {exc}")
        summary["status"] = "error"
        return summary

    # --- audit (N=3 CATTS) + persist --------------------------------------
    try:
        job_id = str(uuid.uuid4())
        agg = await audit_service.run_audit(
            domain, job_id=job_id, n=3, report_id=str(company_id)
        )
        async with AsyncSessionLocal() as db:
            co = await audit_service.persist_audit_result(db, audit_id, company_id, agg)
            if co:
                co.outbound_status = "audited"
                await db.commit()
        summary["score"] = agg.get("score")
        summary["status"] = "audited"
    except Exception as exc:
        logger.warning(f"[scout] audit failed for {domain}: {exc}")
        # Leave whatever state we reached; don't abort the rest of the batch.
        return summary

    # --- enrich the founder -----------------------------------------------
    enrichment = {}
    try:
        async with AsyncSessionLocal() as db:
            co = await db.get(Company, company_id)
            if co:
                co.outbound_status = "enriching"
                await db.commit()
        enrichment = await enrich_founder(domain)
    except Exception as exc:
        logger.warning(f"[scout] enrichment failed for {domain}: {exc}")
        enrichment = {}

    founder_email = (enrichment or {}).get("founder_email")
    try:
        async with AsyncSessionLocal() as db:
            co = await db.get(Company, company_id)
            if co:
                if enrichment:
                    co.founder_name = enrichment.get("founder_name")
                    co.founder_email = enrichment.get("founder_email")
                    co.founder_title = enrichment.get("founder_title")
                    co.enrichment_confidence = enrichment.get("confidence")
                # No email → stay "audited" (honest: nothing to send).
                co.outbound_status = "audited" if not founder_email else co.outbound_status
                await db.commit()
    except Exception as exc:
        logger.warning(f"[scout] persisting enrichment failed for {domain}: {exc}")

    # --- contact (only if we have a real email) ---------------------------
    if not founder_email:
        summary["status"] = "audited"
        return summary

    try:
        async with AsyncSessionLocal() as db:
            co = await db.get(Company, company_id)
            if co and co.founder_email:
                sent = await send_audit_email_for_company(db, co, agg)
                if sent:
                    summary["status"] = "contacted"
                    summary["contacted"] = True
    except Exception as exc:
        logger.warning(f"[scout] outbound failed for {domain}: {exc}")

    return summary


async def run_scout(category: str = "developer tools", count: int = 3) -> dict:
    """Run ONE autonomous scout cycle for ``category``.

    Discovers up to ``count`` new targets, then for each: audits (N=3),
    enriches, and cold-emails the founder if an email was found. Fully
    defensive — one target failing never aborts the batch, and the whole call
    never raises.

    Returns a summary:
        {
          "category": str,
          "discovered": int,
          "audited": int,
          "contacted": int,
          "targets": [{"domain", "score", "status"}],
        }
    """
    logger.info(f"[scout] cycle start — category={category!r} count={count}")
    try:
        targets = await discover_targets(category, count)
    except Exception as exc:
        logger.warning(f"[scout] discovery failed: {exc}")
        targets = []

    results: list[dict] = []
    for target in targets:
        try:
            results.append(await _process_target(target))
        except Exception as exc:  # belt-and-suspenders — never abort the batch
            logger.warning(f"[scout] target {target.get('domain')} crashed: {exc}")
            results.append(
                {"domain": target.get("domain", ""), "score": None, "status": "error"}
            )

    audited = sum(1 for r in results if r.get("status") in ("audited", "contacted"))
    contacted = sum(1 for r in results if r.get("status") == "contacted")
    summary = {
        "category": category,
        "discovered": len(targets),
        "audited": audited,
        "contacted": contacted,
        "targets": [
            {"domain": r.get("domain"), "score": r.get("score"), "status": r.get("status")}
            for r in results
        ],
    }
    logger.info(
        f"[scout] cycle done — discovered={summary['discovered']} "
        f"audited={summary['audited']} contacted={summary['contacted']}"
    )
    return summary
