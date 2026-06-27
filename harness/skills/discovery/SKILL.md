# Discovery — find SaaS companies that are poor agent-readiness targets

**Description:** Given a category/seed (e.g. "developer tools", "fintech APIs", "no-code
platforms"), propose N candidate SaaS companies that are LIKELY to score poorly on
agent-readiness — the best targets for an audit + outbound.

---

## When to use

Top of the funnel. We want a list of real companies whose products an autonomous agent
probably *can't* use, so the audit will find true, specific gaps worth emailing about.

---

## What makes a strong target

A strong target is a real, reachable product that an agent would struggle to use. Bias
toward companies showing these tells:

- **No public API / no developer docs** — the product is UI-only.
- **CAPTCHA / email-verify / SMS-OTP signup** — an agent can't self-serve a credential.
- **No MCP server, no `/llms.txt`, no `/openapi.json`** — no machine surface.
- **Human-in-the-loop core action** — the main job requires clicking through a UI.
- **Mid-market SaaS / vertical tools / no-code / ops dashboards** — categories that
  historically ship UI-first and lag on agent-readiness.

**Avoid weak targets:** infra/devtools giants that are already agent-native (they'll score
high — Stripe, Twilio, GitHub, Vercel, Linear, OpenAI, etc.), and dead/parked domains.

---

## Methodology

1. Interpret the seed into a concrete sub-segment (e.g. "fintech APIs" → expense
   management, invoicing, KYC, lending dashboards).
2. Enumerate real, currently-operating companies in that segment from public knowledge.
   Prefer ones you can name a real domain for.
3. For each candidate, reason about *why* it's likely a poor agent-readiness target — tie
   the reason to a concrete tell above (UI-only, CAPTCHA signup, no docs, etc.).
4. (Optional, if time) quick-probe a candidate's `/openapi.json` and `/llms.txt` to confirm
   they're missing — strengthens the reason. Don't over-invest; the audit does the real work.
5. De-dup, drop anything you can't name a plausible domain for, and rank strongest first.

Return the number requested (default ~10 if unspecified). Real domains only — no invented
companies, no placeholder domains.

---

## Hard rules

- **Real companies with real domains.** No fabricated names or `example.com` placeholders.
- Each `reason` is ONE concrete line tied to an agent-readiness tell — not "popular app".
- Skip companies that are obviously already agent-native (would score high → bad target).
- `domain` is the bare apex (e.g. `acme.com`), no scheme, no path.

---

## OUTPUT CONTRACT — write ONLY this to `/output.json`, then STOP

```json
{
  "targets": [
    {"domain": "acmeinvoicing.com", "name": "Acme Invoicing", "reason": "UI-only invoicing tool, no public API or docs link in nav; signup is email-verify gated."},
    {"domain": "ledgerlypay.com", "name": "Ledgerly", "reason": "Fintech dashboard with reCAPTCHA on signup and no /openapi.json or /llms.txt — closed to agents."}
  ]
}
```

Then stop.
