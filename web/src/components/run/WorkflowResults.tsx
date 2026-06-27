"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { WORKFLOW_LABELS, type WorkflowName } from "@/lib/run-events";

export type WorkflowResult = {
  workflow: WorkflowName;
  passed: boolean;
  evidence: string;
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
        {results.map((r, i) => (
          <li
            key={r.workflow}
            className="flex items-center gap-3 px-4 py-2.5"
            style={{ borderTop: i === 0 ? "none" : "1px solid var(--border)" }}
          >
            {r.passed ? (
              <CheckCircle2
                className="h-4 w-4 shrink-0"
                style={{ color: "var(--success)" }}
              />
            ) : (
              <XCircle
                className="h-4 w-4 shrink-0"
                style={{ color: "var(--danger)" }}
              />
            )}
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
              className="data ml-auto shrink-0 text-[10px] uppercase tracking-[0.08em]"
              style={{ color: r.passed ? "var(--success)" : "var(--danger)" }}
            >
              {r.passed ? "pass" : "fail"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
