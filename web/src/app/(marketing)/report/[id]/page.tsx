import { notFound } from "next/navigation";
import { CheckCircle2, XCircle, Minus, ArrowRight, Globe } from "lucide-react";
import Link from "next/link";

const BACKEND_URL = process.env.BACKEND_URL ?? "";

/* ── Types ─────────────────────────────────────────────────────────────────── */

type DimResult = {
  dimension: string;
  passed: boolean;
  confidence: number;
  weight?: number;
  evidence?: {
    summary?: string;
    detail?: string;
    file?: string;
    line?: number;
  };
  needs_live?: boolean;
};

type ReportData = {
  id: string;
  domain: string;
  score: number;
  confidence: number;
  created_at: string;
  is_post_fix?: boolean;
  dimensions: DimResult[];
};

/* ── Fetch ──────────────────────────────────────────────────────────────────── */

async function fetchReport(id: string): Promise<ReportData | null> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/report/${id}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<ReportData>;
  } catch {
    return null;
  }
}

/* ── Score badge ─────────────────────────────────────────────────────────────── */

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70
      ? "oklch(0.52 0.17 152)"
      : score >= 50
        ? "oklch(0.68 0.18 62)"
        : "oklch(0.53 0.22 20)";

  const label =
    score >= 70 ? "Agent-Ready" : score >= 50 ? "Needs Work" : "Not Ready";

  return (
    <div className="flex items-center gap-4">
      <div
        className="font-display text-7xl font-bold leading-none data"
        style={{ color }}
      >
        {score}
      </div>
      <div>
        <div className="eyebrow text-[10px]" style={{ color: "var(--muted-foreground)" }}>
          agent-readiness score
        </div>
        <div
          className="mt-1 font-display text-sm font-semibold uppercase tracking-widest"
          style={{ color }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}

/* ── Dimension row ───────────────────────────────────────────────────────────── */

function DimRow({ dim }: { dim: DimResult }) {
  const label = dim.dimension.replace(/_/g, " ");

  return (
    <div
      className="border-b px-4 py-4 last:border-b-0"
      style={{ borderColor: "var(--border)" }}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className="mt-0.5 shrink-0">
          {dim.needs_live ? (
            <Minus
              className="h-4 w-4"
              style={{ color: "var(--muted-foreground)" }}
            />
          ) : dim.passed ? (
            <CheckCircle2
              className="h-4 w-4"
              style={{ color: "oklch(0.52 0.17 152)" }}
            />
          ) : (
            <XCircle
              className="h-4 w-4"
              style={{ color: "oklch(0.53 0.22 20)" }}
            />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium capitalize">{label}</span>
            {dim.weight !== undefined && (
              <span
                className="data text-[10px] uppercase tracking-wider"
                style={{ color: "var(--primary)" }}
              >
                {dim.weight}%
              </span>
            )}
            {dim.needs_live && (
              <span
                className="text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5"
                style={{
                  background: "var(--surface-3)",
                  color: "var(--muted-foreground)",
                }}
              >
                Needs live test
              </span>
            )}
          </div>

          {dim.evidence?.summary && (
            <p
              className="mt-1 text-xs leading-relaxed"
              style={{ color: "var(--muted-foreground)" }}
            >
              {dim.evidence.summary}
            </p>
          )}

          {dim.evidence?.file && (
            <div
              className="mt-1.5 font-mono text-[11px]"
              style={{ color: "var(--fg-subtle)" }}
            >
              {dim.evidence.file}
              {dim.evidence.line !== undefined && `:${dim.evidence.line}`}
            </div>
          )}
        </div>

        {/* Confidence */}
        <div
          className="data shrink-0 text-xs"
          style={{ color: "var(--fg-subtle)" }}
        >
          {Math.round(dim.confidence * 100)}%
        </div>
      </div>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────────── */

export default async function ReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const report = await fetchReport(id);

  if (!report) {
    notFound();
  }

  const passCount = report.dimensions.filter((d) => d.passed && !d.needs_live).length;
  const failCount = report.dimensions.filter((d) => !d.passed && !d.needs_live).length;
  const liveCount = report.dimensions.filter((d) => d.needs_live).length;
  const createdAt = new Date(report.created_at).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return (
    <div
      className="min-h-screen"
      style={{ background: "var(--background)", color: "var(--foreground)" }}
    >
      {/* Nav */}
      <nav
        className="sticky top-0 z-40 border-b"
        style={{
          background: "oklch(0.985 0.004 235 / 0.9)",
          backdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-12 max-w-4xl items-center gap-4 px-6">
          <Link
            href="/"
            className="font-display text-sm font-bold uppercase tracking-wider"
          >
            AgentReady
          </Link>
          <ArrowRight
            className="h-3.5 w-3.5"
            style={{ color: "var(--muted-foreground)" }}
          />
          <span
            className="font-mono text-xs"
            style={{ color: "var(--muted-foreground)" }}
          >
            {report.domain}
          </span>
        </div>
      </nav>

      <div className="mx-auto max-w-4xl px-6 py-12">
        {/* Header */}
        <div
          className="rounded border p-6"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-1)",
          }}
        >
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="space-y-3">
              <div className="eyebrow">Audit report</div>
              <div className="flex items-center gap-2">
                <Globe
                  className="h-4 w-4"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <span className="font-mono text-sm">{report.domain}</span>
              </div>
              <ScoreBadge score={report.score} />
            </div>

            {/* Stats */}
            <div className="flex gap-6 text-center">
              <div>
                <div
                  className="font-display text-2xl font-bold data"
                  style={{ color: "oklch(0.52 0.17 152)" }}
                >
                  {passCount}
                </div>
                <div className="eyebrow mt-1 text-[10px]">Pass</div>
              </div>
              <div>
                <div
                  className="font-display text-2xl font-bold data"
                  style={{ color: "oklch(0.53 0.22 20)" }}
                >
                  {failCount}
                </div>
                <div className="eyebrow mt-1 text-[10px]">Fail</div>
              </div>
              {liveCount > 0 && (
                <div>
                  <div
                    className="font-display text-2xl font-bold data"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {liveCount}
                  </div>
                  <div className="eyebrow mt-1 text-[10px]">Needs live</div>
                </div>
              )}
            </div>
          </div>

          <div
            className="mt-4 pt-4 border-t flex items-center gap-6 text-xs"
            style={{
              borderColor: "var(--border)",
              color: "var(--fg-subtle)",
            }}
          >
            <span>Audited {createdAt}</span>
            <span>·</span>
            <span>Confidence {Math.round(report.confidence * 100)}%</span>
            <span>·</span>
            <span>{report.dimensions.length} dimensions</span>
            {report.is_post_fix && (
              <>
                <span>·</span>
                <span
                  className="uppercase tracking-wider text-[10px]"
                  style={{ color: "oklch(0.52 0.17 152)" }}
                >
                  Post-fix verified
                </span>
              </>
            )}
          </div>
        </div>

        {/* Dimension breakdown */}
        <div className="mt-6">
          <div className="eyebrow mb-3">Dimension breakdown</div>
          <div
            className="overflow-hidden rounded border"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
          >
            {report.dimensions.map((dim) => (
              <DimRow key={dim.dimension} dim={dim} />
            ))}
          </div>
        </div>

        {/* CTA */}
        <div
          className="mt-8 rounded border p-6 text-center"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-2)",
          }}
        >
          <div className="eyebrow mb-2">Ready to fix?</div>
          <h3 className="font-display text-xl font-bold mb-3">
            Get a fix PR for every failing dimension.
          </h3>
          <p
            className="mb-6 text-sm mx-auto max-w-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            Connect your GitHub repo, merge the PR, and earn a verified
            agent-ready badge.
          </p>
          <Link
            href={`/signin?callbackUrl=${encodeURIComponent(
              `/onboarding?domain=${report.domain}`
            )}`}
            className="group relative inline-flex items-center gap-2 overflow-hidden rounded-xl bg-foreground px-5 py-2.5 text-sm font-medium text-background transition-transform duration-200 hover:-translate-y-px"
          >
            <span>Fix my product</span>
            <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>
    </div>
  );
}
