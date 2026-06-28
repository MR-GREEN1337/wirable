"use client";

import { Check } from "lucide-react";
import { PHASES, type PhaseName, type ClassifyKind } from "@/lib/run-events";
import { classifyConcept } from "@/lib/run-icons";

export type PhaseState = "pending" | "active" | "done";

interface PhaseTimelineProps {
  states: Record<PhaseName, PhaseState>;
  classify?: { kind: ClassifyKind; evidence: string } | null;
  proxyUnlocked: boolean;
}

function Node({ state, gated }: { state: PhaseState; gated: boolean }) {
  if (state === "done") {
    return (
      <span
        className="relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
        style={{ background: "var(--success)", color: "var(--success-foreground)" }}
      >
        <Check className="h-3 w-3" strokeWidth={3} />
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative z-10 flex h-6 w-6 shrink-0 items-center justify-center">
        <span
          className="absolute h-6 w-6 rounded-full"
          style={{ background: "var(--primary)", opacity: 0.18, animation: "live-pulse 2s cubic-bezier(0.16,1,0.3,1) infinite" }}
        />
        <span
          className="flex h-6 w-6 items-center justify-center rounded-full"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          <span className="h-2 w-2 rounded-full" style={{ background: "var(--primary-foreground)" }} />
        </span>
      </span>
    );
  }
  return (
    <span
      className="relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border"
      style={{
        borderColor: gated ? "var(--border)" : "var(--border-strong)",
        background: "var(--surface-1)",
      }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: gated ? "var(--border-strong)" : "var(--fg-subtle)" }}
      />
    </span>
  );
}

export function PhaseTimeline({ states, classify, proxyUnlocked }: PhaseTimelineProps) {
  return (
    <div
      className="rounded-md border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-2.5"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="eyebrow">Pipeline</span>
        {classify &&
          (() => {
            const c = classifyConcept(classify.kind);
            const Icon = c.icon;
            return (
              <span
                className="data inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] uppercase tracking-[0.08em]"
                style={{ border: `1px solid ${c.accent}`, color: c.accent }}
                title={classify.evidence}
              >
                <Icon className="h-3 w-3" strokeWidth={2} />
                {classify.kind === "api" ? "API path" : "Site path"}
              </span>
            );
          })()}
      </div>

      <ol className="flex flex-col px-3 py-2">
        {PHASES.map((p, i) => {
          const state = states[p.key];
          const isProxyPhase = i >= 3;
          const gated = isProxyPhase && !proxyUnlocked && state === "pending";
          const last = i === PHASES.length - 1;
          const nextDone = !last && states[PHASES[i + 1].key] !== "pending";
          return (
            <li key={p.key} className="relative flex items-start gap-3 pb-1.5 pt-1.5">
              {/* connector line */}
              {!last && (
                <span
                  className="absolute left-3 top-7 -ml-px h-[calc(100%-12px)] w-px"
                  style={{
                    background:
                      state === "done" || nextDone
                        ? "var(--success)"
                        : "var(--border)",
                    opacity: gated ? 0.4 : 1,
                  }}
                />
              )}
              <Node state={state} gated={gated} />
              <div
                className="flex min-w-0 flex-1 items-center justify-between pt-0.5"
                style={{ opacity: gated ? 0.5 : 1 }}
              >
                <span
                  className="text-[13px] font-medium"
                  style={{
                    color: state === "active" ? "var(--primary)" : "var(--foreground)",
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
                      : gated
                        ? "gated"
                        : "queued"}
                </span>
              </div>
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
