"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export type TerminalLine = {
  type: "ok" | "err" | "info" | "warn" | "dim";
  msg: string;
  dim?: string;
};

export type AuditShot = {
  seq: number;
  caption: string;
  dimension?: string;
  url?: string;
  image: string;
};

interface AuditTerminalProps {
  domain: string;
  lines: TerminalLine[];
  score?: number;
  confidence?: number;
  screenshots?: AuditShot[];
  className?: string;
}

function ScoreDisplay({ score }: { score: number }) {
  const color =
    score >= 70
      ? "oklch(0.52 0.17 152)" // green
      : score >= 50
        ? "oklch(0.68 0.18 62)" // amber
        : "oklch(0.53 0.22 20)"; // red

  const label =
    score >= 70 ? "AGENT-READY" : score >= 50 ? "NEEDS WORK" : "NOT READY";

  return (
    <div
      className="flex items-center gap-4 border-t px-4 py-3"
      style={{ borderColor: "var(--t-border)" }}
    >
      <div
        className="font-display text-5xl font-bold leading-none data"
        style={{
          color,
          animation: "score-in 400ms cubic-bezier(0.16,1,0.3,1) both",
        }}
      >
        {score}
      </div>
      <div>
        <div
          className="eyebrow text-[10px]"
          style={{ color: "var(--t-muted)" }}
        >
          agent-readiness score
        </div>
        <div
          className="mt-0.5 font-display text-xs font-semibold uppercase tracking-widest"
          style={{ color }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}

/* ── Dimension badge — color-coded chip overlaid on the live viewport ───────── */

function dimColor(dim?: string): string {
  // Stable hue per dimension so the badge color reads as a "lane"
  if (!dim) return "var(--t-blue)";
  const map: Record<string, string> = {
    auth: "#fbbf24",
    docs: "#60a5fa",
    api: "#4ade80",
    onboarding: "#a78bfa",
    pricing: "#f472b6",
    errors: "#f87171",
    navigation: "#34d399",
    forms: "#fb923c",
  };
  const key = dim.toLowerCase();
  return map[key] ?? "var(--t-blue)";
}

/* ── Live browser viewport — the showpiece ──────────────────────────────────── */

function BrowserViewport({
  shot,
  live,
}: {
  shot: AuditShot;
  live: boolean;
}) {
  const accent = dimColor(shot.dimension);
  const urlText = shot.url ?? shot.caption;

  return (
    <div
      className="flex h-full flex-col overflow-hidden"
      style={{ background: "var(--t-s1)" }}
    >
      {/* Faux browser chrome */}
      <div
        className="flex items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--t-border)", background: "var(--t-bg)" }}
      >
        <span className="h-2.5 w-2.5 rounded-full bg-[#f87171]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#fbbf24]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#4ade80]" />
        <div
          className="ml-2 flex h-6 flex-1 items-center gap-2 overflow-hidden rounded px-2"
          style={{ background: "var(--t-s1)", border: "1px solid var(--t-border)" }}
        >
          <span
            className="shrink-0"
            style={{ color: live ? "var(--t-green)" : "var(--t-muted)", fontSize: 9 }}
          >
            ●
          </span>
          <span
            className="truncate font-mono"
            style={{ color: "var(--t-fg)", fontSize: 11 }}
          >
            {urlText}
          </span>
        </div>
        {live && (
          <span
            className="ml-1 flex shrink-0 items-center gap-1 text-[10px] font-semibold uppercase tracking-widest"
            style={{ color: "var(--t-green)" }}
          >
            <span
              className="inline-block h-1.5 w-1.5 rounded-full bg-[#4ade80]"
              style={{ animation: "cursor-blink 1.2s ease-in-out infinite" }}
            />
            live
          </span>
        )}
      </div>

      {/* The screenshot — cross-fades via cn-enter, keyed by seq */}
      <div
        className="relative flex-1 overflow-hidden"
        style={{ background: "var(--t-bg)", minHeight: 220 }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={shot.seq}
          src={shot.image}
          alt={shot.caption}
          className="absolute inset-0 h-full w-full"
          style={{
            objectFit: "contain",
            animation: "cn-enter 200ms cubic-bezier(0.16,1,0.3,1) both",
          }}
        />

        {/* Caption + dimension badge overlaid at the bottom */}
        <div
          className="absolute inset-x-0 bottom-0 flex items-center gap-2 px-3 py-2"
          style={{
            background:
              "linear-gradient(to top, oklch(0.08 0.005 250 / 0.92), transparent)",
          }}
        >
          {shot.dimension && (
            <span
              className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest"
              style={{
                color: accent,
                border: `1px solid ${accent}`,
                background: "oklch(0.08 0.005 250 / 0.6)",
              }}
            >
              {shot.dimension}
            </span>
          )}
          <span
            className="truncate font-mono text-[11px]"
            style={{ color: "var(--t-fg)" }}
          >
            {shot.caption}
          </span>
          <span
            className="ml-auto shrink-0 font-mono text-[10px] data"
            style={{ color: "var(--t-muted)" }}
          >
            #{shot.seq}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Filmstrip — recent thumbnails; click to pin ────────────────────────────── */

function Filmstrip({
  shots,
  activeSeq,
  onPick,
}: {
  shots: AuditShot[];
  activeSeq: number;
  onPick: (seq: number | null) => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);
  // Cap at the 12 most recent
  const recent = shots.slice(-12);

  useEffect(() => {
    // Auto-scroll to the end as new frames land
    stripRef.current?.scrollTo({
      left: stripRef.current.scrollWidth,
      behavior: "smooth",
    });
  }, [shots.length]);

  return (
    <div
      ref={stripRef}
      className="scrollbar-minimal flex gap-1.5 overflow-x-auto border-t px-2 py-2"
      style={{ borderColor: "var(--t-border)", background: "var(--t-bg)" }}
    >
      {recent.map((s) => {
        const active = s.seq === activeSeq;
        return (
          <button
            key={s.seq}
            type="button"
            onClick={() => onPick(active ? null : s.seq)}
            title={s.caption}
            className="cn-hover relative h-12 w-20 shrink-0 overflow-hidden rounded"
            style={{
              border: active
                ? `1px solid ${dimColor(s.dimension)}`
                : "1px solid var(--t-border)",
              outline: active ? `1px solid ${dimColor(s.dimension)}` : "none",
              opacity: active ? 1 : 0.7,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={s.image}
              alt={s.caption}
              className="h-full w-full"
              style={{ objectFit: "cover" }}
            />
            <span
              className="absolute bottom-0 right-0 px-1 font-mono text-[8px] data"
              style={{
                color: "var(--t-fg)",
                background: "oklch(0.08 0.005 250 / 0.7)",
              }}
            >
              {s.seq}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function AuditTerminal({
  domain,
  lines,
  score,
  confidence,
  screenshots = [],
  className,
}: AuditTerminalProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  // null = follow latest; number = pinned to a specific frame
  const [pinned, setPinned] = useState<number | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  const isRunning = lines.length > 0 && score === undefined;
  const hasShots = screenshots.length > 0;

  // Sorted by seq so the "latest" is deterministic
  const ordered = [...screenshots].sort((a, b) => a.seq - b.seq);
  const latest = ordered[ordered.length - 1];
  const active =
    pinned !== null
      ? ordered.find((s) => s.seq === pinned) ?? latest
      : latest;

  return (
    <div
      className={cn(
        "overflow-hidden rounded border font-mono text-xs",
        className
      )}
      style={{
        background: "var(--t-bg)",
        borderColor: "var(--t-border)",
      }}
    >
      <div className={cn(hasShots && "grid lg:grid-cols-[1.15fr_1fr]")}>
        {/* LEFT — live browser viewport (only once a frame arrives) */}
        {hasShots && active && (
          <div
            className="flex flex-col border-b lg:border-b-0 lg:border-r"
            style={{ borderColor: "var(--t-border)" }}
          >
            <BrowserViewport shot={active} live={isRunning && pinned === null} />
            <Filmstrip
              shots={ordered}
              activeSeq={active.seq}
              onPick={setPinned}
            />
          </div>
        )}

        {/* RIGHT — terminal log + score */}
        <div className="flex min-w-0 flex-col">
          {/* Chrome bar */}
          <div
            className="flex items-center gap-2 border-b px-3 py-2"
            style={{ borderColor: "var(--t-border)", background: "var(--t-s1)" }}
          >
            {!hasShots && (
              <>
                <span className="h-2.5 w-2.5 rounded-full bg-[#f87171]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#fbbf24]" />
                <span className="h-2.5 w-2.5 rounded-full bg-[#4ade80]" />
              </>
            )}
            <span
              className={cn("text-[11px]", !hasShots && "ml-2")}
              style={{ color: "var(--t-muted)" }}
            >
              wirable — {domain || "awaiting domain"}
            </span>
            {isRunning && (
              <span
                className="ml-auto text-[10px] uppercase tracking-widest"
                style={{
                  color: "var(--t-blue)",
                  animation: "cursor-blink 1.2s ease-in-out infinite",
                }}
              >
                live
              </span>
            )}
          </div>

          {/* Lines */}
          <div className="scrollbar-minimal h-56 flex-1 overflow-y-auto px-3 py-3">
            {lines.length === 0 ? (
              <div
                className="select-none text-[11px]"
                style={{ color: "var(--t-muted)" }}
              >
                <span>$ wirable run {domain || "<domain>"}</span>
                <span
                  className="ml-0.5 inline-block h-3 w-1.5 align-middle"
                  style={{
                    background: "var(--t-muted)",
                    animation: "cursor-blink 1s step-end infinite",
                  }}
                />
              </div>
            ) : (
              lines.map((line, i) => (
                <div
                  key={i}
                  className="flex min-w-0 items-baseline gap-2 leading-relaxed"
                  title={`${line.dim ? `[${line.dim}] ` : ""}${line.msg}`}
                  style={{
                    animation: `cn-enter 120ms cubic-bezier(0.16,1,0.3,1) ${Math.min(i * 30, 300)}ms both`,
                  }}
                >
                  <span
                    className="shrink-0 select-none"
                    style={{ color: getLineAccent(line.type) }}
                  >
                    {getLinePrefix(line.type)}
                  </span>
                  {line.dim && (
                    <span
                      className="shrink-0 text-[10px] uppercase tracking-wider"
                      style={{ color: "var(--t-muted)" }}
                    >
                      [{line.dim}]
                    </span>
                  )}
                  {/* One row only — a 2KB blob never wraps the terminal. */}
                  <span
                    className="min-w-0 flex-1 truncate"
                    style={{ color: getLineColor(line.type) }}
                  >
                    {line.msg}
                  </span>
                </div>
              ))
            )}
            {isRunning && (
              <div className="mt-1 flex items-center gap-1">
                <span style={{ color: "var(--t-muted)" }}>$</span>
                <span
                  className="inline-block h-3 w-1.5"
                  style={{
                    background: "var(--t-muted)",
                    animation: "cursor-blink 1s step-end infinite",
                  }}
                />
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Score display */}
          {score !== undefined && <ScoreDisplay score={score} />}

          {score !== undefined && confidence !== undefined && (
            <div
              className="px-4 pb-3 text-[10px]"
              style={{ color: "var(--t-muted)" }}
            >
              confidence {Math.round(confidence * 100)}% · {lines.length} checks
              run
              {hasShots ? ` · ${ordered.length} frames` : ""}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function getLinePrefix(type: TerminalLine["type"]): string {
  switch (type) {
    case "ok":
      return "✓";
    case "err":
      return "✗";
    case "warn":
      return "!";
    case "info":
      return "→";
    case "dim":
      return " ";
  }
}

function getLineAccent(type: TerminalLine["type"]): string {
  switch (type) {
    case "ok":
      return "var(--t-green)";
    case "err":
      return "var(--t-red)";
    case "warn":
      return "var(--t-amber)";
    case "info":
      return "var(--t-blue)";
    case "dim":
      return "var(--t-muted)";
  }
}

function getLineColor(type: TerminalLine["type"]): string {
  switch (type) {
    case "err":
      return "oklch(0.78 0.10 20)";
    case "warn":
      return "oklch(0.85 0.12 70)";
    case "dim":
      return "var(--t-muted)";
    default:
      return "var(--t-fg)";
  }
}
