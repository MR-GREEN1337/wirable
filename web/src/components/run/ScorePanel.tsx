"use client";

import { Check, X } from "lucide-react";
import { scoreColor, scoreLabel, type ScoreDimension } from "@/lib/run-events";
import { DIMENSION_META, dimensionConcept } from "@/lib/run-icons";
import { useCountUp } from "@/lib/use-count-up";

interface ScorePanelProps {
  total: number;
  dimensions: ScoreDimension[];
}

// Collapse whitespace, kill a back-to-back repeated phrase, hard-cap length.
function cleanEvidence(raw: string | undefined): string {
  if (!raw) return "";
  let s = raw.replace(/\s+/g, " ").trim();
  s = s.replace(/(.{8,}?)(?:\s*\1)+/g, "$1");
  return s.length > 240 ? `${s.slice(0, 240)}…` : s;
}

/* ── The ring — an SVG progress ring around the big number ────────────────────*/

function ScoreRing({ value, color }: { value: number; color: string }) {
  const display = useCountUp(value);
  const R = 52;
  const C = 2 * Math.PI * R;
  const offset = C - (display / 100) * C;

  return (
    <div className="relative flex h-32 w-32 shrink-0 items-center justify-center">
      <svg width="128" height="128" viewBox="0 0 128 128" className="-rotate-90">
        <circle
          cx="64"
          cy="64"
          r={R}
          fill="none"
          stroke="var(--border)"
          strokeWidth="6"
        />
        <circle
          cx="64"
          cy="64"
          r={R}
          fill="none"
          stroke={color}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 120ms linear" }}
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span
          className="font-display data leading-none"
          style={{ fontSize: "2.5625rem", color, fontWeight: 700 }}
        >
          {display}
        </span>
        <span className="data text-[11px]" style={{ color: "var(--fg-subtle)" }}>
          / 100
        </span>
      </div>
    </div>
  );
}

export function ScorePanel({ total, dimensions }: ScorePanelProps) {
  const color = scoreColor(total);

  return (
    <div
      className="overflow-hidden rounded-md border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      {/* Big number + ring */}
      <div
        className="flex items-center gap-6 border-b px-6 py-6"
        style={{ borderColor: "var(--border)" }}
      >
        <ScoreRing value={total} color={color} />
        <div className="min-w-0 flex-1">
          <div className="eyebrow mb-1.5">Agent-readiness</div>
          <div
            className="font-display text-[23px] font-semibold leading-tight"
            style={{ color }}
          >
            {scoreLabel(total) === "agent-ready"
              ? "Agent-ready"
              : scoreLabel(total) === "partial"
                ? "Needs work"
                : "Blocked"}
          </div>
          <p
            className="mt-1.5 text-[13px] leading-relaxed"
            style={{ color: "var(--muted-foreground)" }}
          >
            {total >= 70
              ? "Agents can discover, authenticate, and complete the core task."
              : total >= 50
                ? "Agents partially succeed. Key paths still break."
                : "Agents can't reliably drive this product yet."}
          </p>
        </div>
      </div>

      {/* 6 dimensions — each with its own icon + lane color */}
      <ul>
        {dimensions.map((d, i) => {
          const meta = DIMENSION_META[d.dim];
          const concept = dimensionConcept(d.dim);
          const Icon = concept.icon;
          const label = meta?.label ?? d.dim.replace(/_/g, " ");
          const evidence = cleanEvidence(d.evidence);
          return (
            <li
              key={d.dim}
              className="flex items-center gap-3 px-4 py-2.5"
              style={{ borderTop: i === 0 ? "none" : "1px solid var(--border)" }}
            >
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded"
                style={{
                  color: concept.accent,
                  background: `color-mix(in oklch, ${concept.accent} 12%, transparent)`,
                }}
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
              </span>
              <span className="shrink-0 text-[13px] font-medium">{label}</span>
              {meta && (
                <span
                  className="data shrink-0 text-[10px]"
                  style={{ color: "var(--fg-subtle)" }}
                >
                  +{meta.weight}
                </span>
              )}
              {evidence && (
                <span
                  className="min-w-0 flex-1 truncate text-right text-[12px]"
                  style={{ color: "var(--muted-foreground)" }}
                  title={evidence}
                >
                  {evidence}
                </span>
              )}
              <span
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full"
                style={{
                  background: d.passed ? "var(--success)" : "var(--danger)",
                  color: "#fff",
                }}
              >
                {d.passed ? (
                  <Check className="h-2.5 w-2.5" strokeWidth={3} />
                ) : (
                  <X className="h-2.5 w-2.5" strokeWidth={3} />
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
