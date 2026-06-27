"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { Radar, Loader2, ArrowUpRight, Mail } from "lucide-react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

/* ── Types ──────────────────────────────────────────────────────────────────── */

type OutboundStatus =
  | "discovered"
  | "auditing"
  | "audited"
  | "enriching"
  | "enriched"
  | "contacted"
  | string;

type Target = {
  id: string;
  domain: string;
  name?: string;
  score?: number | null;
  confidence?: number | null;
  outbound_status?: OutboundStatus;
  founder_name?: string | null;
  founder_email?: string | null;
  founder_title?: string | null;
  reason?: string | null;
  created_at?: string;
};

/* ── Color logic (shared with score displays) ───────────────────────────────── */

function scoreColor(s?: number | null): string {
  if (s === undefined || s === null)
    return "var(--muted-foreground)";
  return s >= 70
    ? "oklch(0.52 0.17 152)"
    : s >= 50
      ? "oklch(0.68 0.18 62)"
      : "oklch(0.53 0.22 20)";
}

/* The autonomous pipeline, in order. Color tracks the stage of the loop. */
const PIPELINE: { key: string; label: string; color: string }[] = [
  { key: "discovered", label: "discovered", color: "var(--muted-foreground)" },
  { key: "auditing", label: "auditing", color: "oklch(0.68 0.18 62)" },
  { key: "audited", label: "audited", color: "var(--primary)" },
  { key: "enriching", label: "enriching", color: "oklch(0.68 0.18 62)" },
  { key: "enriched", label: "enriched", color: "var(--primary)" },
  { key: "contacted", label: "contacted", color: "oklch(0.52 0.17 152)" },
];

function statusMeta(status?: OutboundStatus) {
  const found = PIPELINE.find((p) => p.key === status);
  return found ?? { key: status ?? "—", label: status ?? "—", color: "var(--muted-foreground)" };
}

function StatusBadge({ status }: { status?: OutboundStatus }) {
  const meta = statusMeta(status);
  const animate = status === "auditing" || status === "enriching";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-widest"
      style={{
        color: meta.color,
        border: `1px solid color-mix(in oklch, ${meta.color} 40%, transparent)`,
        background: `color-mix(in oklch, ${meta.color} 8%, transparent)`,
      }}
    >
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{
          background: meta.color,
          animation: animate
            ? "cursor-blink 1.2s ease-in-out infinite"
            : undefined,
        }}
      />
      {meta.label}
    </span>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────────── */

export default function ConsolePage() {
  const { data: session } = useSession();
  const token = session?.backendToken;

  const [category, setCategory] = useState("developer tools");
  const [count, setCount] = useState(10);
  const [targets, setTargets] = useState<Target[]>([]);
  const [scouting, setScouting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const seenIds = useRef<Set<string>>(new Set());

  const fetchTargets = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(
        `${BACKEND_URL}/api/v1/discovery/targets?scout_only=true&limit=100`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        }
      );
      if (!res.ok) return;
      const data = (await res.json()) as { targets?: Target[] };
      const next = data.targets ?? [];
      // Newest first
      next.sort(
        (a, b) =>
          new Date(b.created_at ?? 0).getTime() -
          new Date(a.created_at ?? 0).getTime()
      );
      setTargets(next);
      next.forEach((t) => seenIds.current.add(t.id));
      setLoaded(true);
    } catch {
      // transient — next poll retries
    }
  }, [token]);

  // Poll every ~3s while the loop runs
  useEffect(() => {
    if (!token) return;
    fetchTargets();
    const id = setInterval(fetchTargets, 3000);
    return () => clearInterval(id);
  }, [token, fetchTargets]);

  async function runScout() {
    if (!token || scouting) return;
    setScouting(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/discovery/scout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          category: category.trim() || undefined,
          count: Number.isFinite(count) ? count : undefined,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Give the backend a beat to seed the first row, then refresh
      setTimeout(fetchTargets, 800);
    } catch {
      setError("Couldn't start the scout. Check your connection and try again.");
    } finally {
      setScouting(false);
    }
  }

  const contacted = targets.filter((t) => t.outbound_status === "contacted").length;

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="eyebrow mb-1">Scout — autonomous outbound</div>
          <h1 className="font-display text-2xl font-bold">Mission control</h1>
          <p
            className="mt-1 max-w-lg text-xs leading-relaxed"
            style={{ color: "var(--muted-foreground)" }}
          >
            Scout discovers products, audits each one for agent-readiness,
            enriches the founder, and queues outbound — autonomously. Watch the
            loop run below.
          </p>
        </div>
        {targets.length > 0 && (
          <div className="flex items-center gap-5 text-right">
            <div>
              <div className="font-display text-3xl font-bold data leading-none">
                {targets.length}
              </div>
              <div className="eyebrow mt-1 text-[10px]">targets</div>
            </div>
            <div>
              <div
                className="font-display text-3xl font-bold data leading-none"
                style={{ color: "oklch(0.52 0.17 152)" }}
              >
                {contacted}
              </div>
              <div className="eyebrow mt-1 text-[10px]">contacted</div>
            </div>
          </div>
        )}
      </div>

      {/* Control bar */}
      <div
        className="mb-6 flex flex-wrap items-end gap-3 rounded border p-4"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div className="flex-1 min-w-[200px]">
          <label className="eyebrow mb-1.5 block text-[10px]">Category</label>
          <input
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="developer tools"
            className="h-9 w-full rounded border bg-transparent px-3 font-mono text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/40"
            style={{ borderColor: "var(--border)" }}
          />
        </div>
        <div className="w-24">
          <label className="eyebrow mb-1.5 block text-[10px]">Count</label>
          <input
            type="number"
            min={1}
            max={50}
            value={count}
            onChange={(e) => setCount(parseInt(e.target.value, 10))}
            className="h-9 w-full rounded border bg-transparent px-3 font-mono text-sm data outline-none focus:border-primary focus:ring-1 focus:ring-primary/40"
            style={{ borderColor: "var(--border)" }}
          />
        </div>
        <button
          onClick={runScout}
          disabled={scouting || !token}
          className="inline-flex h-9 items-center gap-2 rounded px-4 text-sm font-medium disabled:opacity-50"
          style={{
            background: "var(--primary)",
            color: "var(--primary-foreground)",
          }}
        >
          {scouting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Radar className="h-4 w-4" />
          )}
          Run scout
        </button>
      </div>

      {error && (
        <div
          className="mb-4 rounded border px-3 py-2 text-xs"
          style={{
            borderColor: "oklch(0.53 0.22 20 / 0.3)",
            background: "oklch(0.53 0.22 20 / 0.06)",
            color: "oklch(0.53 0.22 20)",
          }}
        >
          {error}
        </div>
      )}

      {/* Targets table or empty state */}
      {targets.length === 0 ? (
        <EmptyState loaded={loaded} onRun={runScout} disabled={scouting || !token} />
      ) : (
        <TargetsTable targets={targets} />
      )}
    </div>
  );
}

/* ── Empty state ────────────────────────────────────────────────────────────── */

function EmptyState({
  loaded,
  onRun,
  disabled,
}: {
  loaded: boolean;
  onRun: () => void;
  disabled: boolean;
}) {
  if (!loaded) {
    return (
      <div
        className="flex items-center justify-center gap-2 rounded border py-20 text-xs"
        style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading targets…
      </div>
    );
  }
  return (
    <div
      className="flex flex-col items-center justify-center rounded border py-20 text-center"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <Radar className="mb-4 h-7 w-7" style={{ color: "var(--primary)" }} />
      <div className="eyebrow mb-2">The loop is idle</div>
      <p
        className="mb-6 max-w-md text-sm leading-relaxed"
        style={{ color: "var(--muted-foreground)" }}
      >
        Run the scout to discover products, score each for agent-readiness,
        enrich the founder, and queue outbound — fully autonomously.
      </p>
      <button
        onClick={onRun}
        disabled={disabled}
        className="inline-flex items-center gap-2 rounded px-4 py-2 text-sm font-medium disabled:opacity-50"
        style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
      >
        <Radar className="h-4 w-4" />
        Run scout
      </button>
    </div>
  );
}

/* ── Targets table ──────────────────────────────────────────────────────────── */

function TargetsTable({ targets }: { targets: Target[] }) {
  return (
    <div
      className="overflow-hidden rounded border"
      style={{ borderColor: "var(--border)" }}
    >
      {/* Header row */}
      <div
        className="grid grid-cols-[1.4fr_0.8fr_0.9fr_1.6fr_56px] items-center gap-3 border-b px-4 py-2"
        style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
      >
        {["product", "status", "score", "founder", ""].map((h, i) => (
          <span
            key={i}
            className="eyebrow text-[10px]"
            style={{ color: "var(--fg-subtle)" }}
          >
            {h}
          </span>
        ))}
      </div>

      {targets.map((t, i) => (
        <div
          key={t.id}
          className="grid grid-cols-[1.4fr_0.8fr_0.9fr_1.6fr_56px] items-center gap-3 border-b px-4 py-3 last:border-b-0"
          style={{
            borderColor: "var(--border)",
            background: i % 2 === 0 ? "var(--surface-1)" : "var(--surface-2)",
            animation: "cn-enter 200ms cubic-bezier(0.16,1,0.3,1) both",
          }}
        >
          {/* Product */}
          <div className="min-w-0">
            <div className="truncate font-mono text-sm">{t.domain}</div>
            {t.name && (
              <div
                className="truncate text-xs"
                style={{ color: "var(--muted-foreground)" }}
              >
                {t.name}
              </div>
            )}
          </div>

          {/* Status */}
          <div>
            <StatusBadge status={t.outbound_status} />
          </div>

          {/* Score */}
          <div>
            {t.score !== undefined && t.score !== null ? (
              <span
                className="font-display text-lg font-bold data"
                style={{ color: scoreColor(t.score) }}
              >
                {t.score}
              </span>
            ) : (
              <span
                className="text-xs"
                style={{ color: "var(--fg-subtle)" }}
              >
                —
              </span>
            )}
          </div>

          {/* Founder */}
          <div className="min-w-0">
            {t.founder_name || t.founder_email ? (
              <div className="min-w-0">
                {t.founder_name && (
                  <div className="truncate text-xs font-medium">
                    {t.founder_name}
                    {t.founder_title && (
                      <span
                        className="ml-1.5 font-normal"
                        style={{ color: "var(--fg-subtle)" }}
                      >
                        {t.founder_title}
                      </span>
                    )}
                  </div>
                )}
                {t.founder_email && (
                  <div
                    className="flex items-center gap-1 truncate font-mono text-[11px]"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    <Mail className="h-3 w-3 shrink-0" />
                    {t.founder_email}
                  </div>
                )}
              </div>
            ) : (
              <span
                className="text-xs"
                style={{ color: "var(--fg-subtle)" }}
              >
                {t.outbound_status === "enriching" ? "enriching…" : "—"}
              </span>
            )}
          </div>

          {/* Report link */}
          <div className="text-right">
            <Link
              href={`/report/${t.id}`}
              className="cn-hover inline-flex h-7 w-7 items-center justify-center rounded"
              style={{ color: "var(--muted-foreground)" }}
              title="View report"
            >
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      ))}
    </div>
  );
}
