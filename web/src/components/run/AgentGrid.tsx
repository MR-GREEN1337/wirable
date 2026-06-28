"use client";

// The live "consensus" view: ALL N CATTS agents browsing side by side.
//
// The run fans out N parallel agents (default 3). Each emits screenshot events
// tagged with its `agent` index; frames are de-duped upstream by `${agent}:${seq}`
// (see RunView reducer). This grid splits the flat shot list by agent and renders
// one compact LiveAgentViewport per agent — so the viewer SEES the consensus
// being formed, not just agent 0's camera.
//
// Layout: 3-up on desktop, 2-up on tablet, stacked on mobile. The first agent
// (the "camera", deep mission) is the primary tile; the rest are fast-probe
// peers. Lyra styling: surface elevation + ciel-bleu, no decorative shadows.
// prefers-reduced-motion is honored inside LiveAgentViewport itself.

import { useMemo } from "react";
import type { AuditShot } from "@/components/AuditTerminal";
import { LiveAgentViewport } from "@/components/run/LiveAgentViewport";

export function AgentGrid({
  domain,
  shots,
  live,
  /** Number of agents in the consensus (default 3 — matches run_audit n=3). */
  n = 3,
}: {
  domain: string;
  shots: AuditShot[];
  live: boolean;
  n?: number;
}) {
  // Group shots by agent index. Always render at least `n` tiles so the grid is
  // populated from the first frame (or even before any land — each tile shows
  // its own boot state). If a run ever escalates a 4th agent, include it too.
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
      shots: map.get(i) ?? [],
    }));
  }, [shots, n]);

  return (
    <div className="flex flex-col gap-2.5">
      <div
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        style={{ gridAutoRows: "1fr" }}
      >
        {byAgent.map(({ agent, shots: agentShots }) => (
          <LiveAgentViewport
            key={agent}
            domain={domain}
            shots={agentShots}
            live={live}
            compact
            label={`Agent ${agent + 1}`}
          />
        ))}
      </div>
      <p
        className="data text-[11px]"
        style={{ color: "var(--muted-foreground)" }}
      >
        consensus · N={byAgent.length}
      </p>
    </div>
  );
}
