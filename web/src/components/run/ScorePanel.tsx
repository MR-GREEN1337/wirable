"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import {
  scoreColor,
  scoreLabel,
  type ScoreDimension,
} from "@/lib/run-events";

// Human labels + weights for the 6 contract dimensions (CONTRACTS.md §b).
const DIM_META: Record<string, { label: string; weight: number }> = {
  api_surface: { label: "API surface", weight: 20 },
  auth: { label: "Agent auth", weight: 20 },
  error_quality: { label: "Error quality", weight: 15 },
  idempotency: { label: "Idempotency", weight: 15 },
  mcp_availability: { label: "MCP availability", weight: 20 },
  docs: { label: "Agent docs", weight: 10 },
};

interface ScorePanelProps {
  total: number;
  dimensions: ScoreDimension[];
}

export function ScorePanel({ total, dimensions }: ScorePanelProps) {
  const color = scoreColor(total);

  return (
    <div
      className="rounded border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      {/* Big number */}
      <div
        className="flex items-end gap-4 border-b px-5 py-5"
        style={{ borderColor: "var(--border)" }}
      >
        <div>
          <div className="eyebrow mb-2">Agent-readiness</div>
          <div className="flex items-end gap-2">
            <span
              className="font-display data leading-none"
              style={{
                fontSize: "3.25rem",
                color,
                fontWeight: 700,
                animation: "score-in 400ms cubic-bezier(0.16,1,0.3,1) both",
              }}
            >
              {total}
            </span>
            <span
              className="data mb-1.5 text-[14px]"
              style={{ color: "var(--fg-subtle)" }}
            >
              /100
            </span>
          </div>
        </div>
        <span
          className="data mb-1.5 ml-auto text-[12px] uppercase tracking-[0.08em]"
          style={{ color }}
        >
          {scoreLabel(total)}
        </span>
      </div>

      {/* hairline meter */}
      <div className="h-0.5 w-full" style={{ background: "var(--border)" }}>
        <div className="h-0.5" style={{ width: `${total}%`, background: color }} />
      </div>

      {/* 6 dimensions */}
      <ul>
        {dimensions.map((d, i) => {
          const meta = DIM_META[d.dim];
          const label = meta?.label ?? d.dim.replace(/_/g, " ");
          return (
            <li
              key={d.dim}
              className="flex items-start gap-3 px-4 py-2.5"
              style={{ borderTop: i === 0 ? "none" : "1px solid var(--border)" }}
            >
              {d.passed ? (
                <CheckCircle2
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: "var(--success)" }}
                />
              ) : (
                <XCircle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: "var(--danger)" }}
                />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium">{label}</span>
                  {meta && (
                    <span
                      className="data text-[10px]"
                      style={{ color: "var(--fg-subtle)" }}
                    >
                      +{meta.weight}
                    </span>
                  )}
                </div>
                {d.evidence && (
                  <p
                    className="mt-0.5 text-[12px] leading-relaxed"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {d.evidence}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
