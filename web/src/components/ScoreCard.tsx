"use client";

import { ArrowRight, GitPullRequest, CheckCircle2, XCircle, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

export type DimResult = {
  passed: boolean;
  needs_live?: boolean;
  label?: string;
};

interface ScoreCardProps {
  beforeScore: number;
  afterScore: number;
  beforeDims: Record<string, DimResult>;
  afterDims: Record<string, DimResult>;
  prUrl?: string;
  prNumber?: number;
  prFiles?: string[];
  className?: string;
}

const DIMENSION_LABELS: Record<string, string> = {
  auth_tokens: "Auth token exposure",
  structured_output: "Structured output",
  error_messages: "Error messages",
  rate_limits: "Rate limit headers",
  llm_txt: "llms.txt / agent manifest",
  webhooks: "Webhook idempotency",
  pagination: "Pagination / cursor",
};

function ScoreRing({
  score,
  label,
  variant = "before",
}: {
  score: number;
  label: string;
  variant?: "before" | "after";
}) {
  const color =
    score >= 70
      ? "oklch(0.52 0.17 152)"
      : score >= 50
        ? "oklch(0.68 0.18 62)"
        : "oklch(0.53 0.22 20)";

  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className="font-display text-6xl font-bold leading-none data"
        style={{ color: variant === "after" ? "oklch(0.52 0.17 152)" : color }}
      >
        {score}
      </div>
      <div className="eyebrow text-[10px]">{label}</div>
    </div>
  );
}

function DimRow({
  dimKey,
  before,
  after,
}: {
  dimKey: string;
  before: DimResult;
  after: DimResult;
}) {
  const label = DIMENSION_LABELS[dimKey] ?? dimKey.replace(/_/g, " ");

  return (
    <div className="flex items-center gap-3 py-1.5 text-xs">
      <DimIcon result={before} />
      <span className="flex-1 text-[var(--muted-foreground)]">{label}</span>
      <DimIcon result={after} />
    </div>
  );
}

function DimIcon({ result }: { result: DimResult }) {
  if (result.needs_live) {
    return (
      <Minus
        className="h-3.5 w-3.5 shrink-0"
        style={{ color: "var(--muted-foreground)" }}
      />
    );
  }
  if (result.passed) {
    return (
      <CheckCircle2
        className="h-3.5 w-3.5 shrink-0"
        style={{ color: "oklch(0.52 0.17 152)" }}
      />
    );
  }
  return (
    <XCircle
      className="h-3.5 w-3.5 shrink-0"
      style={{ color: "oklch(0.53 0.22 20)" }}
    />
  );
}

export function ScoreCard({
  beforeScore,
  afterScore,
  beforeDims,
  afterDims,
  prUrl,
  prNumber,
  prFiles,
  className,
}: ScoreCardProps) {
  const allDimKeys = Array.from(
    new Set([...Object.keys(beforeDims), ...Object.keys(afterDims)])
  );

  return (
    <div className={cn("space-y-4", className)}>
      {/* Score panels */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
        {/* Before */}
        <div
          className="surface-1 rounded border p-4"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="eyebrow mb-3">Before</div>
          <ScoreRing score={beforeScore} label="current score" variant="before" />
        </div>

        {/* Arrow */}
        <ArrowRight
          className="h-5 w-5 shrink-0"
          style={{ color: "var(--primary)" }}
        />

        {/* After */}
        <div
          className="rounded border p-4"
          style={{
            borderColor: "oklch(0.52 0.17 152 / 0.3)",
            background: "oklch(0.52 0.17 152 / 0.04)",
          }}
        >
          <div className="eyebrow mb-3" style={{ color: "oklch(0.52 0.17 152)" }}>
            After merge
          </div>
          <ScoreRing score={afterScore} label="projected score" variant="after" />
        </div>
      </div>

      {/* Dimension breakdown */}
      {allDimKeys.length > 0 && (
        <div
          className="surface-2 rounded border"
          style={{ borderColor: "var(--border)" }}
        >
          <div
            className="grid grid-cols-[auto_1fr_auto] items-center gap-3 border-b px-3 py-2 text-[10px] uppercase tracking-wider"
            style={{
              borderColor: "var(--border)",
              color: "var(--muted-foreground)",
            }}
          >
            <span>now</span>
            <span>dimension</span>
            <span>fixed</span>
          </div>
          <div className="px-3">
            {allDimKeys.map((key) => (
              <DimRow
                key={key}
                dimKey={key}
                before={beforeDims[key] ?? { passed: false }}
                after={afterDims[key] ?? { passed: true }}
              />
            ))}
          </div>
        </div>
      )}

      {/* PR card */}
      {prUrl && (
        <a
          href={prUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="cn-hover block rounded border p-3 transition-colors"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-2)",
          }}
        >
          <div className="flex items-start gap-2">
            <GitPullRequest
              className="mt-0.5 h-4 w-4 shrink-0"
              style={{ color: "oklch(0.52 0.17 152)" }}
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">
                  Fix: agent-readiness patches
                </span>
                {prNumber && (
                  <span
                    className="data text-xs"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    #{prNumber}
                  </span>
                )}
              </div>
              {prFiles && prFiles.length > 0 && (
                <div className="mt-1.5 space-y-0.5">
                  {prFiles.slice(0, 6).map((f) => (
                    <div
                      key={f}
                      className="font-mono text-[11px] truncate"
                      style={{ color: "var(--muted-foreground)" }}
                    >
                      {f}
                    </div>
                  ))}
                  {prFiles.length > 6 && (
                    <div
                      className="text-[11px]"
                      style={{ color: "var(--fg-subtle)" }}
                    >
                      +{prFiles.length - 6} more files
                    </div>
                  )}
                </div>
              )}
            </div>
            <ArrowRight
              className="h-4 w-4 shrink-0 mt-0.5"
              style={{ color: "var(--primary)" }}
            />
          </div>
        </a>
      )}
    </div>
  );
}
