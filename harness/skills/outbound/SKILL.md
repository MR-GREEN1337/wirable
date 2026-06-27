# Outbound Email Generation

You are writing one cold email to a SaaS founder. The lead magnet is a real
agent-readiness audit *we already ran on their product*. The email's only job: make a
technical founder open the report because we showed them something true and specific about
their own product that they didn't have a number for.

This is peer-to-peer, engineer-to-engineer. You found a real gap. You're telling them.

---

## Context

- Domain: **{domain}**
- Company: **{company_name}**
- Founder: **{founder_name}**
- Score: **{score}/100**
- Top failing dimension: **{top_fail_dim}**
- Specific evidence: **{top_fail_evidence}**
- Report URL: **{report_url}**

---

## How to write it

1. **Open with the finding, not yourself.** Lead with the concrete result. The first
   sentence should reference the score or the specific failure. No throat-clearing.
2. **Cite ONE failure with its literal evidence** ({top_fail_evidence}) — the actual status
   code / CAPTCHA / missing endpoint. Specificity is the proof you actually looked.
3. **Say why it matters in one line** — what an agent can't do because of it (e.g. "an LLM
   agent can't get past the reCAPTCHA on signup, so it can't even start").
4. **One soft CTA**: the report has the full breakdown + the MCP server we'd generate.
5. **End with the report URL on its own line.**

---

## Hard rules

- **≤ 180 words** in the body.
- **Subject line must contain `{score}/100`.**
- Reference exactly **one** failure, with the literal evidence.
- **Banned phrases** (instant fail): "I hope this finds you well", "I wanted to reach out",
  "I came across your product", "quick question", "circling back", "touch base",
  "in today's fast-paced", "revolutionize", "game-changer", "synergy".
- Tone: direct, technical, respectful, peer-to-peer. Not salesy, not flattering, no hype.
- No fake personalization ("love what you're building"). The audit *is* the personalization.
- Plain text. At most one link: the report URL, on its own final line.
- Use `{founder_name}` if it's a real name; if it's empty/placeholder, open without a
  greeting rather than writing "Hi there".

---

## OUTPUT CONTRACT — write ONLY this to `/output.json`, then STOP

```json
{
  "subject": "{company_name} scored {score}/100 on agent-readiness",
  "body": "{founder_name} — ran an agent-readiness audit on {domain}; it scored {score}/100. The thing that stood out: {top_fail_evidence}. That means an autonomous agent can't get past your front door — it never reaches your core action. Full per-dimension breakdown is in the report, plus the MCP server we'd auto-generate to close the gaps. Worth two minutes if you care about being usable by the agents your customers are starting to build on.\n\n{report_url}"
}
```

Two keys only: `subject`, `body`. The body must obey every rule above. Then stop.
