"use client";

// The showpiece: the agent's browsing rendered as a CONTINUOUS, LARGE SCREENCAST.
//
// Frames arrive as base64 JPEG data-URLs (the `image` field of screenshot
// events) via the poll loop, captured one-per-agent-step — so they're SPARSE
// (several seconds apart) and sometimes BURSTY (the buffered proxy flushes a
// batch at once). Two failure modes to design against:
//
//   1. "long then sudden stop" — the gap between frames reads as a hang. Fix:
//      keep the last frame on screen ALWAYS (double-buffer), and during a gap
//      run live MOTION that reads as work — a thin indeterminate progress bar
//      across the browser chrome + an "agent working…" caption ticker. The gap
//      now feels alive, not frozen.
//   2. strobing on a burst — if 5 frames land at once we must not flash through
//      all of them. Fix: when the queue jumps by >2, snap to the LATEST with one
//      quick fade and skip the intermediates.
//
// The stage is a WIDE 16:9 hero (fills container width, min ~520-620px tall on
// desktop) with faux browser chrome on top. objectFit:contain on a dark stage so
// screenshots never distort. A larger filmstrip sits below for scrubbing.
//
// prefers-reduced-motion: no crossfade, no progress animation — just swap the
// frame and show a static "working" dot.

import { useEffect, useRef, useState } from "react";
import type { AuditShot } from "@/components/AuditTerminal";

function dimColor(dim?: string): string {
  if (!dim) return "var(--t-blue)";
  const map: Record<string, string> = {
    auth: "#fbbf24",
    docs: "#60a5fa",
    api: "#4ade80",
    api_surface: "#4ade80",
    mcp_availability: "#22d3ee",
    error_quality: "#f87171",
    idempotency: "#a78bfa",
    onboarding: "#a78bfa",
    pricing: "#f472b6",
    errors: "#f87171",
    navigation: "#34d399",
    forms: "#fb923c",
  };
  return map[dim.toLowerCase()] ?? "var(--t-blue)";
}

// Before the first frame lands the sandbox is still cold-booting. Narrate the
// real setup work instead of a static "awaiting frame" so the wait reads as
// progress, not a hang.
const BOOT_PHASES = [
  "configuring sandbox",
  "installing agent runtime",
  "launching headless browser",
  "warming up the agent",
  "opening the target site",
];

// Shown in the gap between frames once the screencast is live. Cycles slowly so
// the dead time between agent steps feels like ongoing work.
const WORKING_PHASES = [
  "agent working",
  "reading the page",
  "planning next action",
  "interacting",
  "capturing the result",
];

function useTicker(phrases: string[], active: boolean, ms = 2200): string {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!active) {
      setI(0);
      return;
    }
    const t = setInterval(() => setI((n) => (n + 1) % phrases.length), ms);
    return () => clearInterval(t);
  }, [active, phrases.length, ms]);
  return phrases[i % phrases.length];
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/* ── Filmstrip scrubber ─────────────────────────────────────────────────────── */

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
  const recent = shots.slice(-24);

  useEffect(() => {
    stripRef.current?.scrollTo({
      left: stripRef.current.scrollWidth,
      behavior: "smooth",
    });
  }, [shots.length]);

  return (
    <div
      ref={stripRef}
      className="scrollbar-minimal flex w-full gap-2 overflow-x-auto border-t px-3 py-3"
      style={{ borderColor: "var(--t-border)", background: "var(--t-s1)" }}
    >
      {recent.map((s) => {
        const active = s.seq === activeSeq;
        const ac = dimColor(s.dimension);
        return (
          <button
            key={s.seq}
            type="button"
            onClick={() => onPick(active ? null : s.seq)}
            title={s.caption}
            className="cn-hover relative aspect-video h-16 shrink-0 overflow-hidden rounded-md"
            style={{
              border: active ? `1px solid ${ac}` : "1px solid var(--t-border)",
              outline: active ? `1px solid ${ac}` : "none",
              opacity: active ? 1 : 0.55,
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
              className="data absolute bottom-0 right-0 px-1 font-mono text-[9px]"
              style={{
                color: "var(--t-fg)",
                background: "oklch(0.08 0.005 250 / 0.72)",
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

/* ── The player ─────────────────────────────────────────────────────────────── */

export function LiveAgentViewport({
  domain,
  shots,
  live,
  compact = false,
  label,
}: {
  domain: string;
  shots: AuditShot[];
  /** true while the run is still in flight (drives the LIVE badge + follow mode). */
  live: boolean;
  /** Compact tile mode (used in the 3-up AgentGrid): shorter stage, no filmstrip. */
  compact?: boolean;
  /** Optional label rendered in the chrome (e.g. "Agent 1"). */
  label?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const bootPhase = useTicker(BOOT_PHASES, shots.length === 0);

  // null = follow the latest frame (video mode); number = pinned to a scrub pick.
  const [pinned, setPinned] = useState<number | null>(null);

  const ordered = [...shots].sort((a, b) => a.seq - b.seq);
  const latest = ordered[ordered.length - 1];
  const active =
    pinned !== null ? ordered.find((s) => s.seq === pinned) ?? latest : latest;

  // Double-buffered crossfade: two stacked <img>. We flip which layer is "front"
  // each time the displayed frame changes, fading the new layer in over the old.
  // The PREVIOUS frame stays on the back layer, so the stage is NEVER blank once
  // a frame has arrived — that's what kills the "hang" between sparse frames.
  const [layers, setLayers] = useState<{ a: AuditShot | null; b: AuditShot | null }>(
    { a: null, b: null },
  );
  const [front, setFront] = useState<"a" | "b">("a");
  const lastShownSeq = useRef<number | null>(null);

  useEffect(() => {
    if (!active) return;
    if (active.seq === lastShownSeq.current) return;
    lastShownSeq.current = active.seq;

    // Burst handling is implicit: `active` is always the LATEST frame in follow
    // mode, so even if 5 frames arrive in one poll we only ever fade to the most
    // recent one — intermediates are skipped, never strobed through.

    if (reduced) {
      // No crossfade: just place the frame on the front layer.
      setLayers((prev) =>
        front === "a" ? { ...prev, a: active } : { ...prev, b: active },
      );
      return;
    }
    // Put the new frame on the BACK layer, then promote it to front so CSS fades
    // it in over the previous frame.
    setLayers((prev) =>
      front === "a" ? { ...prev, b: active } : { ...prev, a: active },
    );
    const id = requestAnimationFrame(() => setFront((f) => (f === "a" ? "b" : "a")));
    return () => cancelAnimationFrame(id);
  }, [active, reduced, front]);

  const accent = dimColor(active?.dimension);
  const urlText = active?.url ?? active?.caption ?? "";
  const following = pinned === null;
  const isLive = live && following;

  // "Waiting for the next frame" = live + following + at least one frame shown.
  // This is the gap where we run the working-motion so it doesn't read as a hang.
  const waiting = isLive && !!active;
  const workingPhase = useTicker(WORKING_PHASES, waiting);

  // Show the indeterminate progress bar whenever there's live work in flight:
  // both during the cold boot (no frame yet) and in the gaps between frames.
  const showProgress = live && following && !reduced;

  return (
    <div
      className="flex w-full flex-col overflow-hidden rounded-lg border font-mono text-xs"
      style={{ background: "var(--t-bg)", borderColor: "var(--t-border)" }}
    >
      {/* Faux browser chrome */}
      <div className="relative" style={{ background: "var(--t-s1)" }}>
        <div
          className="flex items-center gap-2.5 border-b px-4 py-2.5"
          style={{ borderColor: "var(--t-border)" }}
        >
          {compact ? (
            <span className="h-2 w-2 shrink-0 rounded-full bg-[#fbbf24]" />
          ) : (
            <>
              <span className="h-3 w-3 rounded-full bg-[#f87171]" />
              <span className="h-3 w-3 rounded-full bg-[#fbbf24]" />
              <span className="h-3 w-3 rounded-full bg-[#4ade80]" />
            </>
          )}
          {label && (
            <span
              className="eyebrow ml-1 shrink-0 text-[10px]"
              style={{ color: "var(--t-muted)" }}
            >
              {label}
            </span>
          )}
          <div
            className="ml-2 flex h-7 flex-1 items-center gap-2 overflow-hidden rounded-md px-3"
            style={{
              background: "var(--t-bg)",
              border: "1px solid var(--t-border)",
            }}
          >
            <span
              className="shrink-0"
              style={{
                color: isLive ? "var(--t-green)" : "var(--t-muted)",
                fontSize: 10,
              }}
            >
              ●
            </span>
            <span
              className="truncate font-mono"
              style={{ color: "var(--t-fg)", fontSize: 12 }}
            >
              {urlText || domain || "awaiting first frame"}
            </span>
          </div>
          {isLive ? (
            <span
              className="ml-1 flex shrink-0 items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: "var(--t-green)" }}
            >
              <span
                className="inline-block h-1.5 w-1.5 rounded-full bg-[#4ade80]"
                style={{
                  animation: reduced
                    ? undefined
                    : "cursor-blink 1.2s ease-in-out infinite",
                }}
              />
              live
            </span>
          ) : pinned !== null ? (
            <button
              type="button"
              onClick={() => setPinned(null)}
              className="cn-hover ml-1 shrink-0 rounded-md px-2 py-0.5 text-[11px] uppercase tracking-[0.08em]"
              style={{ color: "var(--t-blue)", border: "1px solid var(--t-border)" }}
            >
              resume
            </button>
          ) : null}
        </div>

        {/* Indeterminate top progress bar — the "the agent is working" motion.
            A ciel-bleu shimmer slides across the bottom edge of the chrome
            whenever a run is live, so a multi-second gap between frames reads as
            ongoing work rather than a frozen screen. Hidden under reduced-motion. */}
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-[2px] overflow-hidden"
          style={{ opacity: showProgress ? 1 : 0 }}
        >
          <span
            className="absolute top-0 h-full w-1/3 rounded-full"
            style={{
              background:
                "linear-gradient(90deg, transparent, var(--t-blue), transparent)",
              animation: showProgress
                ? "phase-progress 1.4s ease-in-out infinite"
                : undefined,
            }}
          />
        </div>
      </div>

      {/* The stage — WIDE 16:9, two stacked <img> that crossfade. min-height keeps
          it a real hero on desktop; aspect-video keeps the shape on any width. */}
      <div
        className="relative w-full overflow-hidden"
        style={{
          background: "var(--t-bg)",
          aspectRatio: "16 / 9",
          minHeight: compact ? 0 : 520,
          maxHeight: compact ? undefined : 620,
        }}
      >
        {!active ? (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center"
          >
            <span
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border"
              style={{ borderColor: "var(--t-border)", color: "var(--t-muted)" }}
            >
              {reduced ? (
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: "var(--t-blue)" }}
                />
              ) : (
                <span
                  className="h-4 w-4 rounded-full border-2 border-current border-t-transparent"
                  style={{ animation: "spinner 0.9s linear infinite" }}
                />
              )}
            </span>
            <span className="text-[13px]" style={{ color: "var(--t-fg)" }}>
              {bootPhase}…
            </span>
            <span
              className="font-mono text-[11px]"
              style={{ color: "var(--t-muted)", opacity: 0.7 }}
            >
              {domain || "<domain>"}
            </span>
          </div>
        ) : (
          <>
            {(["a", "b"] as const).map((layer) => {
              const shot = layers[layer];
              if (!shot) return null;
              const isFront = front === layer;
              return (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={layer}
                  src={shot.image}
                  alt={shot.caption}
                  className="absolute inset-0 h-full w-full"
                  style={{
                    objectFit: "contain",
                    opacity: isFront ? 1 : 0,
                    transition: reduced ? undefined : "opacity 240ms ease-out",
                  }}
                />
              );
            })}

            {/* Working ticker — top-left, only in the gap between live frames.
                Pairs with the progress bar to make the dead time feel alive. */}
            {waiting && (
              <div
                className="cn-enter absolute left-3 top-3 flex items-center gap-2 rounded-md px-2.5 py-1"
                style={{
                  background: "oklch(0.08 0.005 250 / 0.72)",
                  border: "1px solid var(--t-border)",
                }}
              >
                <span
                  className="inline-block h-1.5 w-1.5 rounded-full"
                  style={{
                    background: "var(--t-blue)",
                    animation: reduced
                      ? undefined
                      : "cursor-blink 1.1s ease-in-out infinite",
                  }}
                />
                <span
                  className="font-mono text-[11px]"
                  style={{ color: "var(--t-fg)" }}
                >
                  {workingPhase}
                  {reduced ? "" : "…"}
                </span>
              </div>
            )}

            {/* Caption + dimension + seq overlaid bottom-left, screencast-style */}
            <div
              className="absolute inset-x-0 bottom-0 flex items-center gap-2.5 px-4 py-3"
              style={{
                background:
                  "linear-gradient(to top, oklch(0.08 0.005 250 / 0.92), transparent)",
              }}
            >
              {active.dimension && (
                <span
                  className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em]"
                  style={{
                    color: accent,
                    border: `1px solid ${accent}`,
                    background: "oklch(0.08 0.005 250 / 0.6)",
                  }}
                >
                  {active.dimension}
                </span>
              )}
              <span
                className="truncate font-mono text-[13px]"
                style={{ color: "var(--t-fg)" }}
              >
                {active.caption}
              </span>
              <span
                className="data ml-auto shrink-0 font-mono text-[11px]"
                style={{ color: "var(--t-muted)" }}
              >
                #{active.seq} · {ordered.length}f
              </span>
            </div>
          </>
        )}
      </div>

      {/* Filmstrip scrubber — hidden in compact tile mode (the AgentGrid keeps
          tiles small; the full-size primary viewport keeps the filmstrip). */}
      {!compact && ordered.length > 1 && (
        <Filmstrip shots={ordered} activeSeq={active?.seq ?? -1} onPick={setPinned} />
      )}
    </div>
  );
}
