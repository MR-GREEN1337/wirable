"use client";

// The live "consensus" view: N CATTS agents browsing in parallel, shown as ONE
// big viewport with a switcher to swap between agents.
//
// The run fans out N parallel agents (default 3). Each emits screenshot events
// tagged with its `agent` index; frames are de-duped upstream by `${agent}:${seq}`
// (see RunView reducer). Rather than N cramped tiles, we show the SELECTED agent
// large (full filmstrip + scrub) and a row of thumbnail tabs to swap — each tab
// shows that agent's latest frame, frame count, and a live pulse.
//
// Lyra styling: surface elevation + ciel-bleu, no decorative shadows.

import { useMemo, useState, useEffect } from "react";
import type { AuditShot } from "@/components/AuditTerminal";
import { LiveAgentViewport } from "@/components/run/LiveAgentViewport";

export function AgentGrid({
  domain,
  shots,
  live,
  statusText,
  /** Number of agents in the consensus (default 3 — matches run_audit n=3). */
  n = 3,
}: {
  domain: string;
  shots: AuditShot[];
  live: boolean;
  /** The real current backend step (latest line) — shown in the loading state. */
  statusText?: string;
  n?: number;
}) {
  // Group shots by agent index. Always render at least `n` lanes so the switcher
  // is populated from the first frame. If a run escalates a 4th agent, include it.
  const byAgent = useMemo(() => {
    const map = new Map<number, AuditShot[]>();
    for (const s of shots) {
      const a = s.agent ?? 0;
      const list = map.get(a);
      if (list) list.push(s);
      else map.set(a, [s]);
    }
    const maxAgent = shots.reduce((m, s) => Math.max(m, s.agent ?? 0), n - 1);
    const count = Math.max(n, maxAgent + 1);
    return Array.from({ length: count }, (_, i) => ({
      agent: i,
      shots: (map.get(i) ?? []).slice().sort((a, b) => a.seq - b.seq),
    }));
  }, [shots, n]);

  const [selected, setSelected] = useState(0);

  // While live, auto-follow the agent producing the most frames (the busy one),
  // unless the user has manually picked a lane (we detect that with a flag).
  const [userPicked, setUserPicked] = useState(false);
  useEffect(() => {
    if (!live || userPicked) return;
    let best = 0;
    let bestN = -1;
    for (const a of byAgent) {
      if (a.shots.length > bestN) {
        bestN = a.shots.length;
        best = a.agent;
      }
    }
    setSelected(best);
  }, [byAgent, live, userPicked]);

  const current = byAgent[selected] ?? byAgent[0];

  return (
    <div className="flex flex-col gap-3">
      {/* The big viewport — selected agent, full chrome + filmstrip + scrub. */}
      <LiveAgentViewport
        key={current?.agent ?? 0}
        domain={domain}
        shots={current?.shots ?? []}
        live={live}
        label={`Agent ${(current?.agent ?? 0) + 1}`}
        statusText={statusText}
      />

      {/* Switcher — one tab per agent, with its latest frame as a thumbnail. */}
      {byAgent.length > 1 && (
        <div className="flex flex-wrap items-stretch gap-2">
          {byAgent.map(({ agent, shots: agentShots }) => {
            const latest = agentShots[agentShots.length - 1];
            const isSel = agent === selected;
            return (
              <button
                key={agent}
                type="button"
                onClick={() => {
                  setUserPicked(true);
                  setSelected(agent);
                }}
                className="cn-hover group flex items-center gap-2 rounded-md border p-1.5 pr-2.5 text-left transition-colors duration-[120ms]"
                style={{
                  borderColor: isSel ? "var(--primary)" : "var(--border)",
                  background: isSel ? "var(--primary-soft)" : "var(--surface-1)",
                }}
                aria-pressed={isSel}
              >
                {/* thumbnail */}
                <span
                  className="relative block h-9 w-14 shrink-0 overflow-hidden rounded"
                  style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}
                >
                  {latest?.url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={latest.url}
                      alt={`Agent ${agent + 1} latest frame`}
                      className="h-full w-full object-cover"
                    />
                  ) : null}
                  {live && (
                    <span
                      className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full"
                      style={{ background: "var(--success)" }}
                    />
                  )}
                </span>
                <span className="flex flex-col">
                  <span
                    className="text-[12px] font-medium leading-tight"
                    style={{ color: isSel ? "var(--primary)" : "var(--foreground)" }}
                  >
                    Agent {agent + 1}
                    {agent === 0 && (
                      <span className="ml-1 text-[10px] uppercase tracking-[0.06em]" style={{ color: "var(--muted-foreground)" }}>
                        camera
                      </span>
                    )}
                  </span>
                  <span className="data text-[11px] leading-tight" style={{ color: "var(--muted-foreground)" }}>
                    {agentShots.length} frame{agentShots.length === 1 ? "" : "s"}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      <p className="data text-[11px]" style={{ color: "var(--muted-foreground)" }}>
        consensus · {byAgent.length} agents · viewing Agent {(current?.agent ?? 0) + 1}
      </p>
    </div>
  );
}
