"use client";

// "Fix with GitHub" control on the run page. Kicks the backend harness that
// clones the connected repo and opens a fix PR. Progress (line events) + the
// final fix_pr event arrive on the same /state poll bus — the parent bumps the
// poll epoch via onStarted() so they stream live; FixPrRow renders the result.
//
// States:
//  - GitHub not connected → "Connect GitHub to auto-fix" (begins OAuth).
//  - connected            → selected repo (+ inline picker) and "Open fix PR".
//  - running              → spinner until the fix_pr event arrives (running prop).

import { useEffect, useState } from "react";
import { GitPullRequest, Github, CircleX, CheckCircle2, FilePlus2 } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";
import { BACKEND_URL, type ScoreDimension } from "@/lib/run-events";
import { DIMENSION_META } from "@/lib/run-icons";
import { beginGithubConnect, useGithub } from "@/components/github/GithubConnect";
import { RepoPicker } from "@/components/github/RepoPicker";

// The files every fix PR adds — surfaced up-front so the user knows what's coming.
const FIX_FILES = [
  "llms.txt",
  "AGENTS.md",
  "CLAUDE.md",
  "docs/agent-readiness.md",
  ".well-known/mcp.json",
];

function dimLabel(dim: string): string {
  return DIMENSION_META[dim]?.label ?? dim;
}

export function FixWithGithub({
  runId,
  domain,
  running,
  hasResult,
  dimensions,
  score,
  onStarted,
}: {
  runId: string;
  // the tested domain — used to auto-suggest the matching repo for THIS test.
  domain?: string;
  // true once the fix has been kicked and we're awaiting the fix_pr event.
  running: boolean;
  // true once a fix_pr event has rendered — hides the trigger.
  hasResult: boolean;
  // the scored dimensions — drives the "what we'll fix" preview + clean-state.
  dimensions?: ScoreDimension[];
  // the run's total score (0-100) — shown alongside the preview.
  score?: number;
  onStarted: () => void;
}) {
  const { token, ready, status, listRepos } = useGithub();
  const [submitting, setSubmitting] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Repo is scoped PER TEST — local state, never persisted globally, since each
  // run can target a different product. We seed it by matching the tested domain
  // against the repo list, but the user can override with the picker.
  const [repo, setRepo] = useState<string | null>(null);

  const connected = status?.connected;

  // What this PR will address. Failing dims = passed === false. When dimensions
  // are missing/empty we can't reason about cleanliness, so we fall back to the
  // original behavior (allow the fix, no preview / no clean-state gate).
  const hasDims = Array.isArray(dimensions) && dimensions.length > 0;
  const failing = hasDims ? dimensions!.filter((d) => d.passed === false) : [];
  // "Nothing to fix" only when we HAVE dims and none of them fail.
  const alreadyClean = hasDims && failing.length === 0;

  useEffect(() => {
    if (!connected || !token || repo) return;
    let alive = true;
    void (async () => {
      try {
        const repos = await listRepos();
        if (!alive || !repos.length) return;
        // root domain token, e.g. "crossnode.sh" -> "crossnode"
        const stem = (domain ?? "").split(".")[0]?.toLowerCase() ?? "";
        const match =
          stem.length > 2
            ? repos.find((r) => r.name.toLowerCase().includes(stem) || stem.includes(r.name.toLowerCase()))
            : undefined;
        if (match) setRepo(match.full_name);
      } catch {
        /* picker still works manually */
      }
    })();
    return () => {
      alive = false;
    };
  }, [connected, token, domain, listRepos, repo]);

  async function onConnect() {
    setConnecting(true);
    setError(null);
    try {
      await beginGithubConnect();
    } catch {
      setError("Could not start GitHub connect.");
      setConnecting(false);
    }
  }

  async function onFix() {
    if (!token || !repo) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/run/${runId}/fix`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ repo }),
      });
      if (res.status === 402) {
        setError("Opening the fix PR is a Pro feature. Upgrade to Pro to ship the fix to your repo.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Spin up a fresh poll session so the harness's line/fix_pr events stream.
      onStarted();
    } catch {
      setError("Could not start the fix. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  // Result already rendered downstream (FixPrRow) — nothing to show here.
  if (hasResult) return null;

  const isRunning = running || submitting;

  return (
    <div
      className="rounded-lg border p-5"
      style={{
        borderColor: "color-mix(in oklch, var(--primary) 30%, transparent)",
        background: "var(--surface-1)",
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border"
          style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
        >
          <Github className="h-4.5 w-4.5" strokeWidth={1.75} style={{ width: 18, height: 18 }} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[16px] font-semibold">Fix with GitHub</h3>
          <p
            className="mt-1 text-[13px] leading-relaxed"
            style={{ color: "var(--muted-foreground)" }}
          >
            Run the harness against your repo. It clones, generates the
            agent-readiness files, and opens a pull request. No manual edits.
          </p>

          {!ready ? (
            <p className="mt-3 text-[12px]" style={{ color: "var(--fg-subtle)" }}>
              Loading…
            </p>
          ) : !connected ? (
            <div className="mt-4">
              <CtaButton onClick={onConnect} size="sm" disabled={connecting}>
                {connecting ? "Redirecting…" : "Connect GitHub to auto-fix"}
              </CtaButton>
            </div>
          ) : isRunning ? (
            <div
              className="mt-4 flex items-center gap-3 rounded-md border px-4 py-3 text-[13px]"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              <GitPullRequest className="h-4 w-4 shrink-0" style={{ color: "var(--primary)" }} strokeWidth={1.75} />
              <span>Harness running. Cloning, generating, opening PR…</span>
              <span
                className="ml-auto h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
                style={{ animation: "spinner 0.8s linear infinite", color: "var(--primary)" }}
              />
            </div>
          ) : alreadyClean ? (
            <div
              className="mt-4 flex items-center gap-2.5 rounded-md border px-4 py-3 text-[13px]"
              style={{
                borderColor: "color-mix(in oklch, var(--success) 35%, transparent)",
                background: "color-mix(in oklch, var(--success) 6%, transparent)",
                color: "var(--foreground)",
              }}
            >
              <CheckCircle2
                className="h-4 w-4 shrink-0"
                style={{ color: "var(--success)" }}
                strokeWidth={1.75}
              />
              <span>
                Nothing to fix — already agent-ready
                {typeof score === "number" ? ` (${score}/100)` : ""}.
              </span>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-3.5">
              {/* What we'll fix — sets expectations before the PR runs. */}
              {failing.length > 0 && (
                <div
                  className="flex flex-col gap-2.5 rounded-md border p-3.5"
                  style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
                >
                  <span className="eyebrow text-[10px]">
                    What we&apos;ll fix
                    {typeof score === "number" ? ` · ${score}/100` : ""}
                  </span>
                  <ul className="flex flex-col gap-2">
                    {failing.map((d) => (
                      <li key={d.dim} className="flex items-start gap-2">
                        <CircleX
                          className="mt-0.5 h-3.5 w-3.5 shrink-0"
                          style={{ color: "var(--danger)" }}
                          strokeWidth={1.75}
                        />
                        <div className="min-w-0">
                          <span
                            className="text-[12.5px] font-medium"
                            style={{ color: "var(--foreground)" }}
                          >
                            {dimLabel(d.dim)}
                          </span>
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
                    ))}
                  </ul>

                  <div
                    className="mt-0.5 flex flex-col gap-1.5 border-t pt-2.5"
                    style={{ borderColor: "var(--border)" }}
                  >
                    <span className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
                      The PR will add:
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {FIX_FILES.map((f) => (
                        <span
                          key={f}
                          className="data inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]"
                          style={{
                            borderColor: "var(--border)",
                            background: "var(--surface-1)",
                            color: "var(--muted-foreground)",
                          }}
                        >
                          <FilePlus2 className="h-3 w-3 shrink-0" style={{ color: "var(--success)" }} strokeWidth={1.75} />
                          {f}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              <div className="flex flex-col gap-1.5">
                <span className="eyebrow text-[10px]">Repository for this test</span>
                <RepoPicker selected={repo} listRepos={listRepos} onSelect={setRepo} />
                <span className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
                  Scoped to this run{domain ? ` (${domain})` : ""}. Pick the repo this product lives in.
                </span>
              </div>
              <div>
                <CtaButton onClick={onFix} size="sm" disabled={submitting || !repo}>
                  {repo ? "Open fix PR" : "Choose a repo first"}
                </CtaButton>
              </div>
            </div>
          )}

          {error && (
            <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
