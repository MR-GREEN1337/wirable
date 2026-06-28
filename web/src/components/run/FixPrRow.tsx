"use client";

// Success row surfaced when a `fix_pr` event arrives (the proxy FIX flow opened a
// PR on the user's connected repo). Shows the PR link + the files it added
// (llms.txt, AGENTS.md, docs/agent-readiness.md). On failure, pr_url is "" and
// `error` carries the reason — render a muted failure row instead.

import { GitPullRequest, FileText, ExternalLink, AlertTriangle } from "lucide-react";
import { DiffView } from "./DiffView";

export type FixPr = {
  pr_url: string;
  files: string[];
  branch?: string | null;
  repo?: string | null;
  diff?: string | null;
  error?: string | null;
};

export function FixPrRow({ pr }: { pr: FixPr }) {
  const failed = !pr.pr_url || !!pr.error;

  if (failed) {
    return (
      <div
        className="flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-[13px]"
        style={{
          borderColor: "color-mix(in oklch, var(--danger) 30%, transparent)",
          background: "color-mix(in oklch, var(--danger) 5%, transparent)",
          color: "var(--danger)",
        }}
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} />
        <span>{pr.error || "Could not open the fix PR."}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
    <div
      className="rounded-md border p-4"
      style={{
        borderColor: "color-mix(in oklch, var(--success) 35%, transparent)",
        background: "color-mix(in oklch, var(--success) 6%, transparent)",
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
          style={{ background: "var(--success)", color: "var(--background)" }}
        >
          <GitPullRequest className="h-4 w-4" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <a
            href={pr.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="cn-hover inline-flex items-center gap-1.5 text-[14px] font-medium"
            style={{ color: "var(--foreground)" }}
          >
            PR opened
            {pr.repo && (
              <span className="data" style={{ color: "var(--muted-foreground)" }}>
                · {pr.repo}
                {pr.branch ? `:${pr.branch}` : ""}
              </span>
            )}
            <ExternalLink className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--muted-foreground)" }} />
          </a>
          <p
            className="data mt-0.5 truncate text-[12px]"
            style={{ color: "var(--muted-foreground)" }}
            title={pr.pr_url}
          >
            {pr.pr_url}
          </p>

          {pr.files.length > 0 && (
            <ul className="mt-2.5 flex flex-col gap-1">
              {pr.files.map((f) => (
                <li key={f} className="flex items-center gap-2 text-[12px]">
                  <FileText
                    className="h-3.5 w-3.5 shrink-0"
                    style={{ color: "var(--success)" }}
                    strokeWidth={1.75}
                  />
                  <span className="data" style={{ color: "var(--foreground)" }}>
                    {f}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>

      {pr.diff && pr.diff.trim() && <DiffView diff={pr.diff} />}
    </div>
  );
}
