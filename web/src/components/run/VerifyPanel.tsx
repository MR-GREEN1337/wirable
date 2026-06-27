"use client";

import { ArrowRight, TrendingUp } from "lucide-react";
import { scoreColor } from "@/lib/run-events";

interface VerifyPanelProps {
  before: number;
  after: number;
  delta: number;
}

function Ring({ score, label, success }: { score: number; label: string; success?: boolean }) {
  const color = success ? "var(--success)" : scoreColor(score);
  return (
    <div className="flex flex-col items-center gap-1.5">
      <div
        className="font-display data leading-none"
        style={{ fontSize: "3rem", fontWeight: 700, color }}
      >
        {score}
      </div>
      <div className="eyebrow text-[10px]">{label}</div>
    </div>
  );
}

export function VerifyPanel({ before, after, delta }: VerifyPanelProps) {
  return (
    <div
      className="rounded border"
      style={{
        borderColor: "oklch(0.52 0.17 152 / 0.4)",
        background: "oklch(0.52 0.17 152 / 0.04)",
      }}
    >
      <div
        className="flex items-center gap-2 border-b px-4 py-3"
        style={{ borderColor: "oklch(0.52 0.17 152 / 0.25)" }}
      >
        <TrendingUp className="h-4 w-4" style={{ color: "var(--success)" }} />
        <span className="eyebrow" style={{ color: "var(--success)" }}>
          verified — proxy re-tested live
        </span>
      </div>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-6 py-6">
        <Ring score={before} label="before" />
        <ArrowRight className="h-5 w-5" style={{ color: "var(--primary)" }} />
        <Ring score={after} label="after proxy" success />
      </div>

      <div
        className="border-t px-4 py-2.5 text-center"
        style={{ borderColor: "oklch(0.52 0.17 152 / 0.25)" }}
      >
        <span
          className="data text-[13px] font-semibold"
          style={{ color: delta >= 0 ? "var(--success)" : "var(--danger)" }}
        >
          {delta >= 0 ? "+" : ""}
          {delta}
        </span>
        <span className="ml-1.5 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          points · same rubric, same agents — only the surface changed
        </span>
      </div>
    </div>
  );
}
