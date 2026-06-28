"use client";

import { ArrowRight, TrendingUp } from "lucide-react";
import { scoreColor } from "@/lib/run-events";
import { useCountUp } from "@/lib/use-count-up";

interface VerifyPanelProps {
  before: number;
  after: number;
  delta: number;
}

function Ring({
  score,
  label,
  color,
}: {
  score: number;
  label: string;
  color: string;
}) {
  const display = useCountUp(score);
  const R = 34;
  const C = 2 * Math.PI * R;
  const offset = C - (display / 100) * C;
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative flex h-[88px] w-[88px] items-center justify-center">
        <svg width="88" height="88" viewBox="0 0 88 88" className="-rotate-90">
          <circle cx="44" cy="44" r={R} fill="none" stroke="var(--border)" strokeWidth="5" />
          <circle
            cx="44"
            cy="44"
            r={R}
            fill="none"
            stroke={color}
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 120ms linear" }}
          />
        </svg>
        <span
          className="font-display data absolute leading-none"
          style={{ fontSize: "1.75rem", fontWeight: 700, color }}
        >
          {display}
        </span>
      </div>
      <div className="eyebrow text-[10px]">{label}</div>
    </div>
  );
}

export function VerifyPanel({ before, after, delta }: VerifyPanelProps) {
  return (
    <div
      className="rounded-md border"
      style={{
        borderColor: "color-mix(in oklch, var(--success) 40%, transparent)",
        background: "color-mix(in oklch, var(--success) 4%, transparent)",
      }}
    >
      <div
        className="flex items-center gap-2 border-b px-4 py-3"
        style={{ borderColor: "color-mix(in oklch, var(--success) 25%, transparent)" }}
      >
        <TrendingUp className="h-4 w-4" style={{ color: "var(--success)" }} />
        <span className="eyebrow" style={{ color: "var(--success)" }}>
          verified · proxy re-tested live
        </span>
      </div>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-6 py-6">
        <Ring score={before} label="before" color={scoreColor(before)} />
        <ArrowRight className="h-5 w-5" style={{ color: "var(--primary)" }} />
        <Ring score={after} label="after proxy" color="var(--success)" />
      </div>

      <div
        className="border-t px-4 py-2.5 text-center"
        style={{ borderColor: "color-mix(in oklch, var(--success) 25%, transparent)" }}
      >
        <span
          className="data text-[13px] font-semibold"
          style={{ color: delta >= 0 ? "var(--success)" : "var(--danger)" }}
        >
          {delta >= 0 ? "+" : ""}
          {delta}
        </span>
        <span className="ml-1.5 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          points · same rubric, same agents, only the surface changed
        </span>
      </div>
    </div>
  );
}
