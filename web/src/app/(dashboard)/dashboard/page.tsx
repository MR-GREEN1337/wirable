import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";
import Link from "next/link";
import {
  CheckCircle2,
  XCircle,
  Minus,
  GitPullRequest,
  Plus,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { CtaButton } from "@/components/CtaButton";
import { VerifyButton } from "@/components/VerifyButton";

const BACKEND_URL = process.env.BACKEND_URL ?? "";

/* ── Types ─────────────────────────────────────────────────────────────────── */

type DimSummary = {
  dimension: string;
  passed: boolean;
  needs_live?: boolean;
};

type AuditJob = {
  id: string;
  domain: string;
  score: number;
  confidence: number;
  created_at: string;
  status: "pending" | "running" | "done" | "error";
  dimensions: DimSummary[];
};

type FixJob = {
  id: string;
  audit_id: string;
  pr_url?: string;
  pr_number?: number;
  pr_files?: string[];
  status: "pending" | "running" | "pr_open" | "done" | "error";
  before_score: number;
  after_score: number;
  before_dims: Record<string, { passed: boolean; needs_live?: boolean }>;
  after_dims: Record<string, { passed: boolean; needs_live?: boolean }>;
};

type Company = {
  id: string;
  domain: string;
  founder_name?: string;
  founder_email?: string;
};

type DashboardData = {
  state: "no_client" | "pending" | "pr_open" | "verifying" | "done";
  company?: Company;
  audit?: AuditJob;
  fix?: FixJob;
  github_connected?: boolean;
  github_repo?: string;
  recent_audits?: AuditJob[];
};

/* ── Fetch ──────────────────────────────────────────────────────────────────── */

async function fetchDashboard(token: string): Promise<DashboardData | null> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/dashboard`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<DashboardData>;
  } catch {
    return null;
  }
}

/* ── Sub-components ─────────────────────────────────────────────────────────── */

function ScoreDisplay({
  score,
  label,
}: {
  score: number;
  label: string;
}) {
  const color =
    score >= 70
      ? "oklch(0.52 0.17 152)"
      : score >= 50
        ? "oklch(0.68 0.18 62)"
        : "oklch(0.53 0.22 20)";

  return (
    <div className="flex items-center gap-3">
      <div
        className="font-display text-5xl font-bold leading-none data"
        style={{ color }}
      >
        {score}
      </div>
      <div className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
        {label}
      </div>
    </div>
  );
}

function DimList({ dims }: { dims: DimSummary[] }) {
  return (
    <div className="space-y-1">
      {dims.map((d) => (
        <div key={d.dimension} className="flex items-center gap-2 text-xs">
          {d.needs_live ? (
            <Minus
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "var(--muted-foreground)" }}
            />
          ) : d.passed ? (
            <CheckCircle2
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "oklch(0.52 0.17 152)" }}
            />
          ) : (
            <XCircle
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "oklch(0.53 0.22 20)" }}
            />
          )}
          <span
            className="capitalize"
            style={{ color: "var(--muted-foreground)" }}
          >
            {d.dimension.replace(/_/g, " ")}
          </span>
          {d.needs_live && (
            <span
              className="ml-auto text-[10px] uppercase tracking-wider"
              style={{ color: "var(--fg-subtle)" }}
            >
              needs live
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── States ─────────────────────────────────────────────────────────────────── */

function NoClientState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="eyebrow mb-4">Welcome to AgentReady</div>
      <h1 className="font-display text-3xl font-bold mb-4">
        Set up your product
      </h1>
      <p
        className="mb-8 max-w-md text-sm leading-relaxed"
        style={{ color: "var(--muted-foreground)" }}
      >
        Claim your domain, connect GitHub, and we&apos;ll open a fix PR for
        every failing agent-readiness dimension.
      </p>
      <CtaButton href="/onboarding">Set up your product</CtaButton>
    </div>
  );
}

function PendingState({ audit }: { audit: AuditJob }) {
  return (
    <div>
      <div className="eyebrow mb-2">Current audit</div>
      <div
        className="rounded border p-6"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div className="flex items-center gap-3 mb-4">
          <div
            className="h-2 w-2 rounded-full"
            style={{
              background: "oklch(0.68 0.18 62)",
              animation: "cursor-blink 1s ease-in-out infinite",
            }}
          />
          <span className="font-mono text-sm">{audit.domain}</span>
          <span
            className="text-xs uppercase tracking-wider"
            style={{ color: "var(--muted-foreground)" }}
          >
            {audit.status}
          </span>
        </div>
        <Link
          href={`/audit/${audit.id}`}
          className="group inline-flex items-center gap-2 text-sm"
          style={{ color: "var(--primary)" }}
        >
          View live stream
          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>
    </div>
  );
}

function PrOpenState({ fix, token }: { fix: FixJob; token: string }) {
  const scoreColor = (s: number) =>
    s >= 70
      ? "oklch(0.52 0.17 152)"
      : s >= 50
        ? "oklch(0.68 0.18 62)"
        : "oklch(0.53 0.22 20)";

  return (
    <div className="space-y-6">
      <div>
        <div className="eyebrow mb-2">Fix PR open</div>
        <p
          className="text-sm"
          style={{ color: "var(--muted-foreground)" }}
        >
          Review and merge the PR below. After merging, click &ldquo;Merge and
          verify&rdquo; to run a post-fix audit.
        </p>
      </div>

      {/* Score before/after */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4">
        <div
          className="rounded border p-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <div className="eyebrow mb-2">Before</div>
          <div
            className="font-display text-4xl font-bold data"
            style={{ color: scoreColor(fix.before_score) }}
          >
            {fix.before_score}
          </div>
        </div>
        <ArrowRight
          className="h-5 w-5 shrink-0"
          style={{ color: "var(--primary)" }}
        />
        <div
          className="rounded border p-4"
          style={{
            borderColor: "oklch(0.52 0.17 152 / 0.3)",
            background: "oklch(0.52 0.17 152 / 0.04)",
          }}
        >
          <div className="eyebrow mb-2" style={{ color: "oklch(0.52 0.17 152)" }}>
            After merge
          </div>
          <div
            className="font-display text-4xl font-bold data"
            style={{ color: "oklch(0.52 0.17 152)" }}
          >
            {fix.after_score}
          </div>
        </div>
      </div>

      {/* Dimensions */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div
          className="rounded border p-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <div className="eyebrow mb-3">Now</div>
          <DimList
            dims={Object.entries(fix.before_dims).map(([k, v]) => ({
              dimension: k,
              ...v,
            }))}
          />
        </div>
        <div
          className="rounded border p-4"
          style={{
            borderColor: "oklch(0.52 0.17 152 / 0.2)",
            background: "oklch(0.52 0.17 152 / 0.03)",
          }}
        >
          <div
            className="eyebrow mb-3"
            style={{ color: "oklch(0.52 0.17 152)" }}
          >
            After fix
          </div>
          <DimList
            dims={Object.entries(fix.after_dims).map(([k, v]) => ({
              dimension: k,
              ...v,
            }))}
          />
        </div>
      </div>

      {/* PR card */}
      {fix.pr_url && (
        <div
          className="rounded border p-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
        >
          <div className="flex items-start gap-3">
            <GitPullRequest
              className="mt-0.5 h-4 w-4 shrink-0"
              style={{ color: "oklch(0.52 0.17 152)" }}
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">Fix: agent-readiness patches</span>
                {fix.pr_number && (
                  <span
                    className="data text-xs"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    #{fix.pr_number}
                  </span>
                )}
              </div>
              {fix.pr_files && fix.pr_files.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {fix.pr_files.slice(0, 5).map((f) => (
                    <div
                      key={f}
                      className="font-mono text-[11px] truncate"
                      style={{ color: "var(--fg-subtle)" }}
                    >
                      {f}
                    </div>
                  ))}
                  {fix.pr_files.length > 5 && (
                    <div
                      className="text-[11px]"
                      style={{ color: "var(--fg-subtle)" }}
                    >
                      +{fix.pr_files.length - 5} more
                    </div>
                  )}
                </div>
              )}
            </div>
            <a
              href={fix.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="group inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors"
              style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
            >
              View on GitHub
              <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </a>
          </div>
        </div>
      )}

      {/* Verify CTA */}
      <div
        className="rounded border p-4 flex items-center justify-between gap-4"
        style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
      >
        <div>
          <div className="text-sm font-medium mb-0.5">Merged the PR?</div>
          <div
            className="text-xs"
            style={{ color: "var(--muted-foreground)" }}
          >
            Run a post-fix audit to earn your verified badge.
          </div>
        </div>
        <VerifyButton token={token} />
      </div>
    </div>
  );
}

function VerifyingState({ audit }: { audit?: AuditJob }) {
  return (
    <div>
      <div className="eyebrow mb-2">Verifying fix</div>
      <div
        className="rounded border p-6"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div className="flex items-center gap-3 mb-3">
          <Loader2
            className="h-5 w-5 animate-spin"
            style={{ color: "oklch(0.68 0.18 62)" }}
          />
          <div>
            <div className="text-sm font-medium">Verifying your fix…</div>
            <div
              className="text-xs mt-0.5"
              style={{ color: "var(--muted-foreground)" }}
            >
              Running a post-fix audit to confirm your new score.
              {audit?.domain ? ` · ${audit.domain}` : ""}
            </div>
          </div>
        </div>
        {audit && (
          <Link
            href={`/audit/${audit.id}`}
            className="group inline-flex items-center gap-2 text-sm"
            style={{ color: "var(--primary)" }}
          >
            View live stream
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
        )}
      </div>
    </div>
  );
}

function FallbackState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <Loader2
        className="mb-4 h-6 w-6 animate-spin"
        style={{ color: "var(--muted-foreground)" }}
      />
      <h2 className="font-display text-xl font-bold mb-2">
        Nothing to show yet
      </h2>
      <p
        className="mb-6 max-w-sm text-sm"
        style={{ color: "var(--muted-foreground)" }}
      >
        We couldn&apos;t determine the state of your product. Set it up to get
        started, or run a fresh audit.
      </p>
      <CtaButton href="/onboarding">Set up your product</CtaButton>
    </div>
  );
}

function DoneState({ fix, audit }: { fix: FixJob; audit?: AuditJob }) {
  return (
    <div className="space-y-6">
      <div
        className="rounded border p-6 text-center"
        style={{
          borderColor: "oklch(0.52 0.17 152 / 0.3)",
          background: "oklch(0.52 0.17 152 / 0.04)",
        }}
      >
        <CheckCircle2
          className="mx-auto h-8 w-8 mb-3"
          style={{ color: "oklch(0.52 0.17 152)" }}
        />
        <div className="eyebrow mb-2" style={{ color: "oklch(0.52 0.17 152)" }}>
          Verified
        </div>
        <h2 className="font-display text-2xl font-bold mb-2">
          Agent-Ready Confirmed
        </h2>
        <p
          className="text-sm max-w-sm mx-auto"
          style={{ color: "var(--muted-foreground)" }}
        >
          Your product passed the post-fix audit. Share your verified report
          with AI agent developers.
        </p>

        <div className="mt-6 flex items-center justify-center gap-4">
          <div className="text-center">
            <div
              className="font-display text-4xl font-bold data"
              style={{ color: "oklch(0.52 0.17 152)" }}
            >
              {fix.after_score}
            </div>
            <div className="eyebrow mt-1 text-[10px]">verified score</div>
          </div>
          <ArrowRight
            className="h-5 w-5"
            style={{ color: "var(--muted-foreground)" }}
          />
          <div className="text-center">
            <div
              className="font-display text-2xl font-bold data"
              style={{ color: "var(--muted-foreground)" }}
            >
              {fix.before_score}
            </div>
            <div className="eyebrow mt-1 text-[10px]">was before</div>
          </div>
        </div>
      </div>

      {audit && (
        <div>
          <div className="eyebrow mb-3">Audit dimensions</div>
          <div
            className="rounded border"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
          >
            <DimList dims={audit.dimensions} />
          </div>
        </div>
      )}

      <div className="flex gap-3">
        <CtaButton href="/" size="sm">
          Audit another domain
        </CtaButton>
      </div>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────────── */

export default async function DashboardPage() {
  const session = await auth();
  if (!session?.backendToken) {
    redirect("/api/auth/signin");
  }

  const data = await fetchDashboard(session.backendToken);

  // Fallback: treat fetch failure as no_client state
  const dashboard: DashboardData = data ?? { state: "no_client" };

  return (
    <div>
      <div className="mb-8 flex items-center justify-between">
        <div>
          <div className="eyebrow mb-1">Dashboard</div>
          <h1 className="font-display text-2xl font-bold">
            {session.user?.name
              ? `Welcome, ${session.user.name.split(" ")[0]}`
              : "Your audits"}
          </h1>
        </div>
        {dashboard.state !== "no_client" && (
          <CtaButton href="/" size="sm">
            <Plus className="h-3.5 w-3.5" />
            New audit
          </CtaButton>
        )}
      </div>

      {/* State machine — every backend state renders something */}
      {dashboard.state === "no_client" ? (
        <NoClientState />
      ) : dashboard.state === "pending" && dashboard.audit ? (
        <PendingState audit={dashboard.audit} />
      ) : dashboard.state === "pr_open" && dashboard.fix ? (
        <PrOpenState fix={dashboard.fix} token={session.backendToken} />
      ) : dashboard.state === "verifying" ? (
        <VerifyingState audit={dashboard.audit} />
      ) : dashboard.state === "done" && dashboard.fix ? (
        <DoneState fix={dashboard.fix} audit={dashboard.audit} />
      ) : (
        <FallbackState />
      )}

      {/* Recent audits */}
      {dashboard.recent_audits && dashboard.recent_audits.length > 0 && (
        <div className="mt-12">
          <div className="eyebrow mb-3">Recent audits</div>
          <div
            className="overflow-hidden rounded border"
            style={{ borderColor: "var(--border)" }}
          >
            {dashboard.recent_audits.map((a, i) => {
              const color =
                a.score >= 70
                  ? "oklch(0.52 0.17 152)"
                  : a.score >= 50
                    ? "oklch(0.68 0.18 62)"
                    : "oklch(0.53 0.22 20)";
              return (
                <Link
                  key={a.id}
                  href={`/report/${a.id}`}
                  className="cn-hover flex items-center gap-4 border-b px-4 py-3 last:border-b-0 transition-colors"
                  style={{
                    borderColor: "var(--border)",
                    background: i % 2 === 0 ? "var(--surface-1)" : "var(--surface-2)",
                  }}
                >
                  <span className="font-mono text-sm flex-1">{a.domain}</span>
                  <span
                    className="data text-sm font-bold"
                    style={{ color }}
                  >
                    {a.score}
                  </span>
                  <span
                    className="text-xs"
                    style={{ color: "var(--fg-subtle)" }}
                  >
                    {new Date(a.created_at).toLocaleDateString()}
                  </span>
                  <ArrowRight
                    className="h-3.5 w-3.5"
                    style={{ color: "var(--muted-foreground)" }}
                  />
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
