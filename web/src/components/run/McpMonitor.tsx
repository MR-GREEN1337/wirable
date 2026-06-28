"use client";

// McpMonitor — MCP drift monitoring surfaced inside ProxyPanel.
//
// When a connected repo pushes, our backend webhook fetches the repo's llms.txt,
// finds the advertised MCP endpoint, re-probes it, and records any drift (tools
// added/removed, reachability flips). This panel shows the latest recorded
// status for the connected repo and offers a manual "Check now" re-verify so the
// flow can be demoed without waiting for a real push.

import { useCallback, useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { RefreshCw, Activity, AlertTriangle, GitBranch } from "lucide-react";
import { BACKEND_URL } from "@/lib/run-events";
import { useGithub } from "@/components/github/GithubConnect";

const WEBHOOK_URL = "https://5-161-110-99.sslip.io/api/v1/github/webhook";

type MonitorStatus = {
  repo?: string;
  mcp_url?: string | null;
  reachable?: boolean;
  tool_count?: number;
  tools?: string[];
  added?: string[];
  removed?: string[];
  drift?: boolean;
  reachable_flip?: boolean;
  first_seen?: boolean;
  checked_at?: string;
  error?: string;
  // Code-grounded endpoint diff from the latest commit (set by the push webhook).
  endpoint_changes?: {
    commit_sha?: string;
    prev_commit_sha?: string;
    branch?: string;
    added_count?: number;
    removed_count?: number;
    changed_count?: number;
    added?: Array<{ method?: string; path?: string } | string>;
    removed?: Array<{ method?: string; path?: string } | string>;
    changed?: Array<{ key?: string } | string>;
    changed_since_last_commit?: boolean;
  } | null;
};

function epLabel(e: { method?: string; path?: string } | string): string {
  if (typeof e === "string") return e;
  return `${(e.method || "").toUpperCase()} ${e.path || ""}`.trim();
}

type MonitorResponse = {
  connected: boolean;
  repo?: string;
  checked: boolean;
  status?: MonitorStatus;
};

function relativeTime(iso?: string): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "never";
  const secs = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function McpMonitor() {
  const { data: session } = useSession();
  const token = session?.backendToken;
  const { status: gh, ready } = useGithub();
  const repo = gh?.repo ?? null;

  const [mon, setMon] = useState<MonitorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !repo) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(
        `${BACKEND_URL}/api/v1/github/monitor?repo=${encodeURIComponent(repo)}`,
        { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMon((await res.json()) as MonitorResponse);
    } catch {
      setErr("Could not load monitor status.");
    } finally {
      setLoading(false);
    }
  }, [token, repo]);

  useEffect(() => {
    if (!ready) return;
    void load();
  }, [ready, load]);

  const checkNow = useCallback(async () => {
    if (!token || !repo) return;
    setChecking(true);
    setErr(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/github/monitor/check`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ repo }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { status?: MonitorStatus };
      setMon({ connected: true, repo, checked: true, status: data.status });
    } catch {
      setErr("Re-check failed. Try again.");
    } finally {
      setChecking(false);
    }
  }, [token, repo]);

  // ---- Not connected / no repo -> subtle hint ----------------------------
  if (ready && (!gh?.connected || !repo)) {
    return (
      <div
        className="border-t px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="eyebrow mb-1.5 flex items-center gap-1.5 text-[10px]">
          <Activity className="h-3 w-3" /> drift monitor
        </div>
        <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          Connect a repo to monitor — on every commit we re-verify the MCP your{" "}
          <code className="font-mono">llms.txt</code> points at.
        </p>
      </div>
    );
  }

  const s = mon?.status;
  const reachable = !!s?.reachable;
  const drift = !!s?.drift;
  const added = s?.added ?? [];
  const removed = s?.removed ?? [];

  return (
    <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
      <div className="flex items-center gap-2">
        <div className="eyebrow flex items-center gap-1.5 text-[10px]">
          <Activity className="h-3 w-3" /> drift monitor
        </div>
        {repo && (
          <span
            className="inline-flex items-center gap-1 font-mono text-[11px]"
            style={{ color: "var(--muted-foreground)" }}
          >
            <GitBranch className="h-3 w-3" /> {repo}
          </span>
        )}
        <button
          type="button"
          onClick={checkNow}
          disabled={checking || loading}
          className="cn-hover ml-auto inline-flex shrink-0 items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] font-medium disabled:opacity-50"
          style={{ borderColor: "var(--border-strong)", color: "var(--foreground)" }}
        >
          <RefreshCw
            className={`h-3 w-3 ${checking ? "animate-spin" : ""}`}
          />
          {checking ? "Checking…" : "Check now"}
        </button>
      </div>

      {/* Status row */}
      {mon?.checked && s ? (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {/* health pill */}
            <span
              className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
              style={{
                color: reachable ? "var(--success)" : "var(--danger)",
                border: `1px solid ${reachable ? "var(--success)" : "var(--danger)"}`,
                background: `color-mix(in oklch, ${
                  reachable ? "var(--success)" : "var(--danger)"
                } 10%, transparent)`,
              }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: reachable ? "var(--success)" : "var(--danger)" }}
              />
              {reachable ? "healthy" : "unreachable"}
            </span>

            <span
              className="data text-[11px]"
              style={{ color: "var(--muted-foreground)" }}
            >
              {s.tool_count ?? 0} tools
            </span>

            <span
              className="data text-[11px]"
              style={{ color: "var(--muted-foreground)" }}
            >
              checked {relativeTime(s.checked_at)}
            </span>

            {drift && (
              <span
                className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
                style={{
                  color: "var(--primary)",
                  border: "1px solid var(--primary)",
                  background: "var(--primary-soft)",
                }}
              >
                <AlertTriangle className="h-3 w-3" /> drift
              </span>
            )}
          </div>

          {/* advertised mcp url */}
          {s.mcp_url && (
            <div
              className="mt-2 truncate font-mono text-[11px]"
              style={{ color: "var(--muted-foreground)" }}
              title={s.mcp_url}
            >
              llms.txt → {s.mcp_url}
            </div>
          )}

          {/* drift detail */}
          {drift && (added.length > 0 || removed.length > 0) && (
            <div className="mt-2 flex flex-col gap-1">
              {added.length > 0 && (
                <div className="text-[11px]" style={{ color: "var(--success)" }}>
                  + added: {added.join(", ")}
                </div>
              )}
              {removed.length > 0 && (
                <div className="text-[11px]" style={{ color: "var(--danger)" }}>
                  − removed: {removed.join(", ")}
                </div>
              )}
            </div>
          )}

          {/* code-grounded endpoint diff since the last commit */}
          {(() => {
            const ec = s.endpoint_changes;
            if (!ec) return null;
            const a = ec.added ?? [];
            const r = ec.removed ?? [];
            const c = ec.changed ?? [];
            if (a.length === 0 && r.length === 0 && c.length === 0) return null;
            const sha = (ec.commit_sha || "").slice(0, 7);
            const prev = (ec.prev_commit_sha || "").slice(0, 7);
            return (
              <div
                className="mt-2.5 rounded border px-2.5 py-2"
                style={{ borderColor: "color-mix(in oklch, var(--primary) 35%, transparent)", background: "var(--primary-soft)" }}
              >
                <div className="eyebrow mb-1.5 flex items-center gap-1.5 text-[10px]">
                  <GitBranch className="h-3 w-3" /> endpoints changed
                  {prev && sha && (
                    <span className="data font-mono normal-case" style={{ color: "var(--muted-foreground)" }}>
                      {prev} → {sha}
                    </span>
                  )}
                </div>
                <div className="flex flex-col gap-0.5 font-mono text-[11px]">
                  {a.map((e, i) => (
                    <div key={`a${i}`} style={{ color: "var(--success)" }}>+ {epLabel(e)}</div>
                  ))}
                  {r.map((e, i) => (
                    <div key={`r${i}`} style={{ color: "var(--danger)" }}>− {epLabel(e)}</div>
                  ))}
                  {c.map((e, i) => (
                    <div key={`c${i}`} style={{ color: "var(--warning)" }}>
                      ~ {typeof e === "string" ? e : e.key || "changed"}
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          {/* honest error / empty states */}
          {s.error && (
            <p
              className="mt-2 text-[11px]"
              style={{ color: "var(--muted-foreground)" }}
            >
              {s.error}
            </p>
          )}
        </>
      ) : (
        <p
          className="mt-2 text-[12px]"
          style={{ color: "var(--muted-foreground)" }}
        >
          {loading
            ? "Loading…"
            : "Not checked yet. Push to the repo, or hit Check now."}
        </p>
      )}

      {/* webhook hint */}
      <div
        className="mt-3 rounded border px-2.5 py-2 text-[11px] leading-relaxed"
        style={{
          borderColor: "var(--border)",
          background: "var(--surface-2)",
          color: "var(--muted-foreground)",
        }}
      >
        On every commit we re-verify the MCP your{" "}
        <code className="font-mono">llms.txt</code> points at. Add a webhook in
        the repo (Settings → Webhooks):{" "}
        <code
          className="font-mono"
          style={{ color: "var(--foreground)", wordBreak: "break-all" }}
        >
          {WEBHOOK_URL}
        </code>{" "}
        — content-type <code className="font-mono">application/json</code>, the
        push event.
      </div>

      {err && (
        <p className="mt-2 text-[11px]" style={{ color: "var(--danger)" }}>
          {err}
        </p>
      )}
    </div>
  );
}
