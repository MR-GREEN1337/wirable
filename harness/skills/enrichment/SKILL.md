# Enrichment — find the founder behind a domain

**Description:** Given a domain, find the founder / CEO / CTO's name, email, and title —
best-effort, and brutally honest about confidence. Never fabricate a confident email.

---

## When to use

Before outbound: we have `{domain}` and an audit, and we need a real human + a real (or
best-guess) email to send the lead magnet to. One person, the most senior reachable
decision-maker (founder/CEO > CTO > head of eng).

---

## Methodology

1. **Probe the site for people.** Load the domain and check the pages that name humans:
   ```
   /about   /about-us   /team   /our-team   /company   /people   /leadership   /contact
   ```
   Look in the DOM for names paired with titles (`Founder`, `CEO`, `CTO`, `Co-founder`),
   `mailto:` links, and a public contact email. A `mailto:founder@{domain}` is gold.
2. **Read the footer + legal pages** (`/imprint`, `/privacy`, `/terms`) — these often carry
   a real registrant name and a contact email by law.
3. **Reason from public knowledge.** If you know who founded {company} (well-known
   startups), use it — but mark it as inferred and keep confidence honest.
4. **Infer the email pattern.** If you found one real address at the domain (e.g.
   `jane.doe@{domain}` on the team page), infer the pattern (`first.last@`, `first@`,
   `f.last@`) and apply it to the founder's name. Common patterns, in rough order:
   `first@`, `first.last@`, `firstlast@`, `flast@`.
5. **Prefer a verified email** (found on the site) over a pattern guess. A pattern guess is
   acceptable only with correspondingly lower confidence.

Use the browser (Playwright) and `curl`/`httpx`. Keep it quick — best-effort, not a forensic
investigation. No paid data brokers.

---

## Hard rules

- **NEVER fabricate a confident email.** If you did not see the address and are only
  pattern-guessing, set `confidence ≤ 0.5` and say "pattern guess" in `evidence`.
- A name found on the team page with a verified `mailto:` → confidence 0.85–0.95.
- A name found but email only pattern-inferred → confidence 0.4–0.6.
- No human found at all → return empty name/email, confidence ≤ 0.2, and explain.
- `evidence` must state *how* you found each field (which page, which `mailto:`, or "inferred
  pattern from `jane.doe@` on /team").
- Honest beats optimistic. A wrong confident email burns the domain.

---

## OUTPUT CONTRACT — write ONLY this to `/output.json`, then STOP

```json
{
  "founder_name": "Jane Doe",
  "founder_email": "jane@example.com",
  "founder_title": "Co-founder & CEO",
  "confidence": 0.9,
  "evidence": "Named on /team as 'Jane Doe, Co-founder & CEO' with mailto:jane@example.com in the card; email verified, not guessed."
}
```

Low-confidence example:

```json
{
  "founder_name": "Jane Doe",
  "founder_email": "jane@example.com",
  "founder_title": "CEO",
  "confidence": 0.45,
  "evidence": "Name from /about ('founded by Jane Doe'); no email on site. Pattern guess first@ inferred from support@example.com format — UNVERIFIED."
}
```

Then stop.
