import { Reveal } from "@/components/Reveal";
import { CtaButton } from "@/components/CtaButton";
import { HeroAudit } from "./HeroAudit";
import { Search, ScanLine, Mail, GitPullRequest, CircleCheck } from "lucide-react";

/* ── The standard AgentReady defines ───────────────────────────────────────────
   7 weighted dimensions, summing to 100. The score is the product. */

const RUBRIC: { dim: string; key: string; weight: number; note: string }[] = [
  {
    dim: "Auth",
    key: "auth",
    weight: 20,
    note: "Machine-obtainable credentials. No human-only OAuth dead-ends.",
  },
  {
    dim: "MCP",
    key: "mcp",
    weight: 20,
    note: "A served MCP surface agents can call without a browser.",
  },
  {
    dim: "Discoverability",
    key: "discoverability",
    weight: 15,
    note: "/llms.txt, an agent manifest, a documented entrypoint.",
  },
  {
    dim: "Errors",
    key: "errors",
    weight: 15,
    note: "Machine-readable codes, not HTML stack-trace pages.",
  },
  {
    dim: "Idempotency",
    key: "idempotency",
    weight: 15,
    note: "Retries are safe. Event IDs let agents dedupe.",
  },
  {
    dim: "Rate limits",
    key: "ratelimit",
    weight: 10,
    note: "Remaining + Retry-After headers so agents self-throttle.",
  },
  {
    dim: "Docs",
    key: "docs",
    weight: 5,
    note: "Structured, parseable reference — not a marketing PDF.",
  },
];

/* ── The loop — find → audit → email → fix → verify ──────────────────────────── */

const LOOP = [
  { icon: Search, label: "Find", line: "Surface SaaS that agents can't use." },
  { icon: ScanLine, label: "Audit", line: "N=3 agents reach a consensus score." },
  { icon: Mail, label: "Email", line: "The audit is the cold-email lead magnet." },
  { icon: GitPullRequest, label: "Fix", line: "Generate an MCP server, open a PR." },
  { icon: CircleCheck, label: "Verify", line: "Re-audit. Prove the score went up." },
];

/* score color ramp — green ≥70 / amber 50–69 / rose <50 */
function scoreColor(n: number): string {
  if (n >= 70) return "var(--success)";
  if (n >= 50) return "var(--warning)";
  return "var(--danger)";
}

/* ── Nav ───────────────────────────────────────────────────────────────────── */

function Nav() {
  return (
    <nav
      className="sticky top-0 z-40 border-b"
      style={{
        background: "color-mix(in oklch, var(--surface-1) 82%, transparent)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderColor: "var(--border)",
      }}
    >
      <div className="mx-auto flex h-12 max-w-[1120px] items-center px-6">
        <a
          href="/"
          className="font-display text-sm font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--foreground)" }}
        >
          AgentReady
        </a>

        <div className="ml-8 hidden flex-1 items-center gap-6 text-[13px] sm:flex">
          <a href="#standard" className="cn-hover" style={{ color: "var(--muted-foreground)" }}>
            The standard
          </a>
          <a href="#loop" className="cn-hover" style={{ color: "var(--muted-foreground)" }}>
            The loop
          </a>
          <a href="#proof" className="cn-hover" style={{ color: "var(--muted-foreground)" }}>
            Proof
          </a>
        </div>

        <a
          href="/signin"
          className="ml-auto mr-4 text-[13px] cn-hover sm:ml-0"
          style={{ color: "var(--muted-foreground)" }}
        >
          Sign in
        </a>
        <CtaButton href="#audit" size="sm">
          Run an audit
        </CtaButton>
      </div>
    </nav>
  );
}

/* ── The signature bloom — sky→indigo, low chroma, hero only ──────────────────── */

function HeroBloom() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      <div
        className="absolute left-1/2 top-[-220px] h-[640px] w-[1100px] -translate-x-1/2 opacity-[0.5]"
        style={{
          background:
            "radial-gradient(ellipse 52% 50% at 50% 50%, oklch(0.72 0.13 240 / 0.55) 0%, oklch(0.60 0.10 248 / 0.30) 42%, transparent 72%)",
          filter: "blur(60px)",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage:
            "linear-gradient(var(--foreground) 1px, transparent 1px), linear-gradient(90deg, var(--foreground) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          maskImage:
            "radial-gradient(ellipse 70% 60% at 50% 30%, black, transparent 75%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 70% 60% at 50% 30%, black, transparent 75%)",
        }}
      />
    </div>
  );
}

/* ── A restrained score panel — the score as a typographic object ─────────────── */

function ScorePanel({
  label,
  score,
  sub,
}: {
  label: string;
  score: number;
  sub: string;
}) {
  const color = scoreColor(score);
  return (
    <div
      className="flex flex-col gap-4 p-6"
      style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        <span
          className="data text-[11px] uppercase tracking-[0.08em]"
          style={{ color }}
        >
          {score >= 70 ? "ready" : score >= 50 ? "partial" : "blocked"}
        </span>
      </div>
      <div className="flex items-end gap-2">
        <span
          className="font-display data leading-none"
          style={{ fontSize: "2.5625rem", color, fontWeight: 600 }}
        >
          {score}
        </span>
        <span className="data mb-1 text-[13px]" style={{ color: "var(--fg-subtle)" }}>
          /100
        </span>
      </div>
      {/* hairline meter */}
      <div className="h-px w-full" style={{ background: "var(--border)" }}>
        <div
          className="h-px"
          style={{ width: `${score}%`, background: color }}
        />
      </div>
      <p className="text-[13px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
        {sub}
      </p>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────────── */

export default async function LandingPage() {
  return (
    <div style={{ background: "var(--background)", color: "var(--foreground)" }}>
      <Nav />

      {/* ── Hero — lead with the live product ── */}
      <section id="audit" className="relative overflow-hidden border-b" style={{ borderColor: "var(--border)" }}>
        <HeroBloom />

        <div className="relative mx-auto max-w-[1120px] px-6 pb-20 pt-24">
          <div className="mx-auto max-w-2xl text-center">
            <Reveal>
              <div className="eyebrow mb-5">Agent-readiness, scored</div>
            </Reveal>

            <Reveal delay={40}>
              <h1
                className="font-display font-semibold leading-[1.08] tracking-[-0.02em]"
                style={{ fontSize: "2.5rem", color: "var(--foreground)" }}
              >
                Agents can&apos;t use your product.
              </h1>
            </Reveal>

            <Reveal delay={80}>
              <p
                className="mx-auto mt-5 max-w-lg text-[16px] leading-relaxed"
                style={{ color: "var(--muted-foreground)" }}
              >
                We prove it with a live browser audit, then fix it — a score from
                0&ndash;100 across 7 dimensions, three agents reaching consensus.
              </p>
            </Reveal>
          </div>

          {/* The showpiece — give it a clean frame and room */}
          <Reveal delay={120}>
            <div
              className="mx-auto mt-12 max-w-2xl rounded-lg p-2"
              style={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
              }}
            >
              <div
                className="rounded-md p-4"
                style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}
              >
                <HeroAudit />
              </div>
            </div>
          </Reveal>

          <Reveal delay={160}>
            <p
              className="mx-auto mt-5 text-center text-[13px]"
              style={{ color: "var(--fg-subtle)" }}
            >
              Free audit, no account &middot; connect GitHub for the fix PR
            </p>
          </Reveal>
        </div>
      </section>

      {/* ── The standard — the 7-dimension rubric, dense table ── */}
      <section id="standard" className="border-b" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1120px] px-6 py-24">
          <Reveal>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <div className="eyebrow mb-2">The standard</div>
                <h2
                  className="font-display font-semibold tracking-[-0.02em]"
                  style={{ fontSize: "1.4375rem" }}
                >
                  Seven dimensions. One hundred points.
                </h2>
              </div>
              <p
                className="max-w-sm text-[13px] leading-relaxed"
                style={{ color: "var(--muted-foreground)" }}
              >
                Weighted, deterministic, the same for every product. This is the
                rubric the score is measured against.
              </p>
            </div>
          </Reveal>

          <Reveal delay={40}>
            <div
              className="mt-10 overflow-hidden rounded-md"
              style={{ border: "1px solid var(--border)" }}
            >
              {/* header */}
              <div
                className="grid grid-cols-[1fr_auto] items-center gap-4 px-4 py-2.5 sm:grid-cols-[200px_1fr_64px]"
                style={{
                  background: "var(--surface-2)",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span className="eyebrow">Dimension</span>
                <span className="eyebrow hidden sm:block">What it means</span>
                <span className="eyebrow text-right">Weight</span>
              </div>

              {RUBRIC.map((row, i) => (
                <div
                  key={row.key}
                  className="grid grid-cols-[1fr_auto] items-baseline gap-4 px-4 py-3 sm:grid-cols-[200px_1fr_64px]"
                  style={{
                    background: "var(--surface-1)",
                    borderBottom:
                      i === RUBRIC.length - 1 ? "none" : "1px solid var(--border)",
                  }}
                >
                  <div className="flex items-center gap-3">
                    <span
                      className="data text-[11px]"
                      style={{ color: "var(--fg-subtle)" }}
                    >
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="text-[14px] font-medium">{row.dim}</span>
                  </div>
                  <p
                    className="col-span-2 text-[13px] leading-relaxed sm:col-span-1"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {row.note}
                  </p>
                  <div className="text-right sm:col-start-3 sm:row-start-1">
                    <span
                      className="data text-[14px]"
                      style={{ color: "var(--primary)" }}
                    >
                      {row.weight}
                    </span>
                  </div>
                </div>
              ))}

              {/* total row */}
              <div
                className="grid grid-cols-[1fr_auto] items-center gap-4 px-4 py-2.5 sm:grid-cols-[200px_1fr_64px]"
                style={{
                  background: "var(--surface-2)",
                  borderTop: "1px solid var(--border)",
                }}
              >
                <span className="eyebrow">Total</span>
                <span className="hidden sm:block" />
                <span className="data text-right text-[14px]">100</span>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* ── The loop — shown not told ── */}
      <section
        id="loop"
        className="border-b"
        style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
      >
        <div className="mx-auto max-w-[1120px] px-6 py-24">
          <Reveal>
            <div className="eyebrow mb-2">The loop</div>
            <h2
              className="font-display font-semibold tracking-[-0.02em]"
              style={{ fontSize: "1.4375rem" }}
            >
              An agency that runs itself.
            </h2>
          </Reveal>

          <Reveal delay={40}>
            <div className="mt-12 grid gap-px sm:grid-cols-5" style={{ background: "var(--border)" }}>
              {LOOP.map(({ icon: Icon, label, line }, i) => (
                <div
                  key={label}
                  className="flex flex-col gap-3 p-5"
                  style={{ background: "var(--surface-1)" }}
                >
                  <div className="flex items-center gap-2">
                    <Icon className="h-4 w-4" style={{ color: "var(--primary)" }} strokeWidth={1.75} />
                    <span className="data text-[11px]" style={{ color: "var(--fg-subtle)" }}>
                      {String(i + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <div className="font-display text-[14px] font-semibold">{label}</div>
                  <p className="text-[13px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
                    {line}
                  </p>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      {/* ── Proof — the 31→87 receipt, the moat ── */}
      <section id="proof" className="border-b" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1120px] px-6 py-24">
          <Reveal>
            <div className="eyebrow mb-2">Proof</div>
            <h2
              className="font-display font-semibold tracking-[-0.02em]"
              style={{ fontSize: "1.4375rem" }}
            >
              The score moves. On the record.
            </h2>
            <p
              className="mt-3 max-w-md text-[13px] leading-relaxed"
              style={{ color: "var(--muted-foreground)" }}
            >
              We audit before, open the PR, then re-audit live. Same rubric, same
              agents — the only thing that changed is the product.
            </p>
          </Reveal>

          <Reveal delay={40}>
            <div className="mt-10 grid items-center gap-4 sm:grid-cols-[1fr_auto_1fr]">
              <ScorePanel
                label="Before"
                score={31}
                sub="No MCP surface, OAuth dead-ends, HTML error pages. Agents bounce."
              />
              <div className="flex items-center justify-center py-2">
                <div
                  className="data flex items-center gap-2 px-3 py-1 text-[12px]"
                  style={{
                    border: "1px solid var(--border)",
                    background: "var(--surface-1)",
                    color: "var(--muted-foreground)",
                  }}
                >
                  <span style={{ color: "var(--danger)" }}>31</span>
                  <span>&rarr;</span>
                  <span style={{ color: "var(--success)" }}>87</span>
                </div>
              </div>
              <ScorePanel
                label="After fix PR"
                score={87}
                sub="Served MCP, machine auth, /llms.txt, typed errors. Agents complete the task."
              />
            </div>
          </Reveal>
        </div>
      </section>

      {/* ── Closing CTA — one confident ask ── */}
      <section className="border-b" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1120px] px-6 py-24 text-center">
          <Reveal>
            <h2
              className="font-display font-semibold tracking-[-0.02em]"
              style={{ fontSize: "1.75rem" }}
            >
              Run the audit. See the number.
            </h2>
            <p
              className="mx-auto mt-4 max-w-md text-[14px] leading-relaxed"
              style={{ color: "var(--muted-foreground)" }}
            >
              Paste a domain. Three agents, a live browser, a score in under two
              minutes — no account required.
            </p>
          </Reveal>
          <Reveal delay={40} className="mt-8 flex justify-center">
            <CtaButton href="#audit">Run an audit</CtaButton>
          </Reveal>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer style={{ background: "var(--surface-2)" }}>
        <div
          className="mx-auto flex max-w-[1120px] flex-col items-start justify-between gap-3 px-6 py-8 text-[13px] sm:flex-row sm:items-center"
          style={{ color: "var(--muted-foreground)" }}
        >
          <span className="font-display text-sm font-bold uppercase tracking-[0.08em]" style={{ color: "var(--foreground)" }}>
            AgentReady
          </span>
          <span>The standard for agent-readiness &middot; find, audit, fix, verify.</span>
          <span className="data text-[12px]">&copy; {new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}
