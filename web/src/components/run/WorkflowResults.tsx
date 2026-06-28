"use client";

import { Check, X, UserPlus, Zap, ShieldAlert, RefreshCw, type LucideIcon } from "lucide-react";
import { WORKFLOW_LABELS, type WorkflowName } from "@/lib/run-events";

export type WorkflowResult = {
  workflow: WorkflowName;
  passed: boolean;
  evidence: string;
};

const WORKFLOW_ICONS: Record<WorkflowName, LucideIcon> = {
  signup: UserPlus,
  core_action: Zap,
  error_handling: ShieldAlert,
  retry_idempotency: RefreshCw,
};

export function WorkflowResults({ results }: { results: WorkflowResult[] }) {
  if (results.length === 0) return null;
  return (
    <div
      className="rounded border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div
        className="border-b px-4 py-2"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="eyebrow">Workflow results</span>
      </div>
      <ul>
        {results.map((r, i) => {
          const Icon = WORKFLOW_ICONS[r.workflow] ?? Check;
          return (
            <li
              key={r.workflow}
              className="flex items-center gap-3 px-4 py-2.5"
              style={{ borderTop: i === 0 ? "none" : "1px solid var(--border)" }}
            >
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded"
                style={{
                  color: r.passed ? "var(--success)" : "var(--danger)",
                  background: `color-mix(in oklch, ${r.passed ? "var(--success)" : "var(--danger)"} 12%, transparent)`,
                }}
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
              </span>
              <span className="shrink-0 text-[13px] font-medium">
                {WORKFLOW_LABELS[r.workflow] ?? r.workflow}
              </span>
              {r.evidence && (
                <span
                  className="min-w-0 flex-1 truncate text-[12px]"
                  style={{ color: "var(--muted-foreground)" }}
                  title={r.evidence}
                >
                  {r.evidence}
                </span>
              )}
              <span
                className="data ml-auto flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-[0.08em]"
                style={{ color: r.passed ? "var(--success)" : "var(--danger)" }}
              >
                {r.passed ? <Check className="h-3 w-3" strokeWidth={3} /> : <X className="h-3 w-3" strokeWidth={3} />}
                {r.passed ? "pass" : "fail"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
