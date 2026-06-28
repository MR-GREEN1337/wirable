"use client";

import { useEffect, useRef } from "react";
import { Check, Minus, X } from "lucide-react";
import {
  PROBE_CONCEPT,
  SANDBOX_CONCEPT,
  dimensionConcept,
} from "@/lib/run-icons";
import type { TerminalLine } from "@/components/AuditTerminal";

/* ── Row classification ───────────────────────────────────────────────────────
   The backend streams terse log lines. We promote the recognizable shapes into
   richer, iconned rows; everything else falls through to a clean default row. */

type Row =
  | { kind: "probe"; path: string; present: boolean }
  | { kind: "sandbox"; index: string | null; label: string }
  | { kind: "default"; line: TerminalLine };

function classifyLine(line: TerminalLine): Row {
  const msg = line.msg.trim();

  // probe /openapi.json -> absent|present
  const probe = msg.match(
    /^(?:probe\s+)?(\/\S+|https?:\/\/\S+)\s*(?:->|→|:)\s*(present|absent|found|missing|200|404|ok|fail)\b/i
  );
  if (probe) {
    const verdict = probe[2].toLowerCase();
    const present = ["present", "found", "200", "ok"].includes(verdict);
    return { kind: "probe", path: probe[1], present };
  }

  // [0] spawning sandbox  /  spawning sandbox [0]  /  sandbox spawned
  const sandbox = msg.match(
    /(?:\[(\d+)\]\s*)?(spawn(?:ing)?\s+sandbox|sandbox\s+(?:spawn|ready|started|booting)[\w ]*|booting\s+agent[\w ]*)(?:\s*\[(\d+)\])?/i
  );
  if (sandbox) {
    const index = sandbox[1] ?? sandbox[3] ?? null;
    return { kind: "sandbox", index, label: sandbox[2] };
  }

  return { kind: "default", line };
}

/* ── Individual rows ──────────────────────────────────────────────────────────*/

function ProbeRow({ path, present }: { path: string; present: boolean }) {
  const Icon = PROBE_CONCEPT.icon;
  return (
    <div className="flex items-center gap-2.5">
      <Icon
        className="h-3.5 w-3.5 shrink-0"
        style={{ color: "var(--t-muted)" }}
        strokeWidth={1.75}
      />
      <span className="data min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--t-fg)" }}>
        {path}
      </span>
      <span
        className="data inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em]"
        style={{
          color: present ? "var(--t-green)" : "var(--t-muted)",
          border: `1px solid ${present ? "var(--t-green)" : "var(--t-border)"}`,
        }}
      >
        {present ? <Check className="h-2.5 w-2.5" /> : <Minus className="h-2.5 w-2.5" />}
        {present ? "present" : "absent"}
      </span>
    </div>
  );
}

function SandboxRow({ index, label }: { index: string | null; label: string }) {
  const Icon = SANDBOX_CONCEPT.icon;
  return (
    <div className="flex items-center gap-2.5">
      <span
        className="inline-flex h-5 shrink-0 items-center gap-1.5 rounded px-1.5 text-[10px]"
        style={{
          color: SANDBOX_CONCEPT.accent,
          border: `1px solid ${SANDBOX_CONCEPT.accent}`,
          background: "color-mix(in oklch, var(--t-amber) 8%, transparent)",
        }}
      >
        <Icon className="h-3 w-3" strokeWidth={1.75} />
        {index !== null && <span className="data">#{index}</span>}
      </span>
      <span className="min-w-0 flex-1 truncate text-[12px] lowercase" style={{ color: "var(--t-fg)" }}>
        {label}
      </span>
    </div>
  );
}

function DefaultRow({ line }: { line: TerminalLine }) {
  const concept = line.dim ? dimensionConcept(line.dim) : null;
  const color =
    line.type === "err"
      ? "var(--t-red)"
      : line.type === "ok"
        ? "var(--t-green)"
        : line.type === "warn"
          ? "var(--t-amber)"
          : "var(--t-muted)";
  return (
    <div className="flex items-center gap-2.5">
      {line.type === "err" ? (
        <X className="h-3.5 w-3.5 shrink-0" style={{ color }} strokeWidth={2} />
      ) : line.type === "ok" ? (
        <Check className="h-3.5 w-3.5 shrink-0" style={{ color }} strokeWidth={2} />
      ) : (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: color, marginLeft: 4, marginRight: 4 }}
        />
      )}
      {line.dim && concept && (
        <span
          className="data inline-flex shrink-0 items-center gap-1 rounded px-1.5 text-[9px] uppercase tracking-[0.08em]"
          style={{ color: concept.accent, border: `1px solid ${concept.accent}` }}
        >
          <concept.icon className="h-2.5 w-2.5" strokeWidth={2} />
          {line.dim}
        </span>
      )}
      <span
        className="min-w-0 flex-1 truncate text-[12px]"
        style={{ color: line.type === "dim" ? "var(--t-muted)" : "var(--t-fg)" }}
      >
        {line.msg}
      </span>
    </div>
  );
}

/* ── The stream ───────────────────────────────────────────────────────────────*/

export function ActivityStream({
  lines,
  running,
}: {
  lines: TerminalLine[];
  running: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  return (
    <div
      className="rounded-md border"
      style={{ borderColor: "var(--t-border)", background: "var(--t-bg)" }}
    >
      <div
        className="flex items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--t-border)" }}
      >
        <span className="eyebrow text-[10px]" style={{ color: "var(--t-muted)" }}>
          activity
        </span>
        <span className="data ml-auto text-[10px]" style={{ color: "var(--t-muted)" }}>
          {lines.length} events
        </span>
      </div>

      <div className="scrollbar-minimal flex max-h-[420px] min-h-[140px] flex-col gap-1.5 overflow-y-auto px-3 py-3 font-mono">
        {lines.length === 0 ? (
          <div className="select-none text-[11px]" style={{ color: "var(--t-muted)" }}>
            <span>$ wirable run · awaiting stream</span>
            <span
              className="ml-0.5 inline-block h-3 w-1.5 align-middle"
              style={{
                background: "var(--t-muted)",
                animation: "cursor-blink 1s step-end infinite",
              }}
            />
          </div>
        ) : (
          lines.map((line, i) => {
            const row = classifyLine(line);
            return (
              <div
                key={i}
                title={`${line.dim ? `[${line.dim}] ` : ""}${line.msg}`}
                style={{
                  animation: `cn-enter 160ms cubic-bezier(0.16,1,0.3,1) ${Math.min(i * 18, 240)}ms both`,
                }}
              >
                {row.kind === "probe" ? (
                  <ProbeRow path={row.path} present={row.present} />
                ) : row.kind === "sandbox" ? (
                  <SandboxRow index={row.index} label={row.label} />
                ) : (
                  <DefaultRow line={row.line} />
                )}
              </div>
            );
          })
        )}
        {running && (
          <div className="mt-0.5 flex items-center gap-1.5">
            <span style={{ color: "var(--t-muted)" }}>$</span>
            <span
              className="inline-block h-3 w-1.5"
              style={{
                background: "var(--t-blue)",
                animation: "cursor-blink 1s step-end infinite",
              }}
            />
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
