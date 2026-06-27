"use client";

import { PHASES, type PhaseName, type ClassifyKind } from "@/lib/run-events";

export type PhaseState = "pending" | "active" | "done";

interface PhaseTimelineProps {
  states: Record<PhaseName, PhaseState>;
  classify?: { kind: ClassifyKind; evidence: string } | null;
  // Whether the proxy half (generate/deploy/verify) has been unlocked yet.
  proxyUnlocked: boolean;
}

function Dot({ state }: { state: PhaseState }) {
  if (state === "done") {
    return (
      <span
        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full"
        style={{ background: "var(--success)", color: "var(--success-foreground)" }}
      >
        <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
          <path
            d="M2.5 6.2L4.8 8.5L9.5 3.5"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative flex h-4 w-4 shrink-0 items-center justify-center">
        <span
          className="absolute h-4 w-4 rounded-full"
          style={{
            background: "var(--primary)",
            opacity: 0.25,
            animation: "cn-hover 1200ms ease-in-out infinite alternate",
          }}
        />
        <span
          className="h-2 w-2 rounded-full"
          style={{ background: "var(--primary)" }}
        />
      </span>
    );
  }
  return (
    <span
      className="h-4 w-4 shrink-0 rounded-full border"
      style={{ borderColor: "var(--border-strong)" }}
    />
  );
}

export function PhaseTimeline({ states, classify, proxyUnlocked }: PhaseTimelineProps) {
  return (
    <div
      className="rounded border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="eyebrow">Workflow</span>
        {classify && (
          <span
            className="data inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] uppercase tracking-[0.08em]"
            style={{
              border: "1px solid var(--primary)",
              color: "var(--primary)",
              background: "var(--primary-soft)",
            }}
            title={classify.evidence}
          >
            {classify.kind === "api" ? "API path" : "Site path"}
          </span>
        )}
      </div>

      <ol className="flex flex-col">
        {PHASES.map((p, i) => {
          const state = states[p.key];
          const isProxyPhase = i >= 3;
          const dimmed = isProxyPhase && !proxyUnlocked && state === "pending";
          return (
            <li
              key={p.key}
              className="flex items-center gap-3 px-3 py-2.5"
              style={{
                borderTop: i === 0 ? "none" : "1px solid var(--border)",
                opacity: dimmed ? 0.45 : 1,
              }}
            >
              <Dot state={state} />
              <span
                className="flex-1 text-[13px] font-medium"
                style={{
                  color:
                    state === "active"
                      ? "var(--primary)"
                      : "var(--foreground)",
                }}
              >
                {p.label}
              </span>
              <span
                className="data text-[10px] uppercase tracking-[0.08em]"
                style={{
                  color:
                    state === "done"
                      ? "var(--success)"
                      : state === "active"
                        ? "var(--primary)"
                        : "var(--fg-subtle)",
                }}
              >
                {state === "done"
                  ? "done"
                  : state === "active"
                    ? "running"
                    : isProxyPhase && !proxyUnlocked
                      ? "gated"
                      : "queued"}
              </span>
            </li>
          );
        })}
      </ol>

      {classify && (
        <div
          className="border-t px-3 py-2 text-[11px] leading-relaxed"
          style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
        >
          <span className="eyebrow mr-1.5 text-[10px]">classify</span>
          {classify.evidence}
        </div>
      )}
    </div>
  );
}
