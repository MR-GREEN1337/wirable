"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { NormalizedEnvelope } from "@/lib/run-events";

export type ToolCall = {
  name: string;
  request: unknown;
  response: unknown;
  normalized: NormalizedEnvelope;
};

function pretty(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function CodeBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="eyebrow mb-1 text-[10px]" style={{ color: "var(--t-muted)" }}>
        {label}
      </div>
      <pre
        className="scrollbar-minimal max-h-48 overflow-auto rounded px-2 py-1.5 font-mono text-[11px] leading-relaxed"
        style={{
          background: "var(--t-bg)",
          border: "1px solid var(--t-border)",
          color: "var(--t-fg)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {pretty(value)}
      </pre>
    </div>
  );
}

function EnvelopePill({ env }: { env: NormalizedEnvelope }) {
  const color = env.success ? "var(--t-green)" : "var(--t-red)";
  return (
    <div
      className="rounded px-2 py-1.5"
      style={{ background: "var(--t-s1)", border: `1px solid ${color}` }}
    >
      <div className="flex items-center gap-2">
        <span
          className="data text-[11px] font-semibold uppercase tracking-[0.08em]"
          style={{ color }}
        >
          {env.success ? "success" : "fail"}
        </span>
        {env.retryable && (
          <span
            className="data rounded px-1 text-[9px] uppercase tracking-[0.08em]"
            style={{ color: "var(--t-amber)", border: "1px solid var(--t-amber)" }}
          >
            retryable
          </span>
        )}
      </div>
      {env.error_code && (
        <div className="mt-1 font-mono text-[10px]" style={{ color: "var(--t-muted)" }}>
          code: <span style={{ color: "var(--t-fg)" }}>{env.error_code}</span>
        </div>
      )}
    </div>
  );
}

export function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const env = call.normalized;
  const accent = env.success ? "var(--t-green)" : "var(--t-red)";

  return (
    <div
      className="overflow-hidden rounded border font-mono"
      style={{ borderColor: "var(--t-border)", background: "var(--t-s1)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className="h-3.5 w-3.5 shrink-0 transition-transform"
          style={{
            color: "var(--t-muted)",
            transform: open ? "rotate(90deg)" : "none",
          }}
        />
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: accent }}
        />
        <span className="truncate text-[12px]" style={{ color: "var(--t-fg)" }}>
          {call.name}
        </span>
        <span
          className="data ml-auto shrink-0 text-[10px] uppercase tracking-[0.08em]"
          style={{ color: accent }}
        >
          {env.success ? "ok" : env.error_code ?? "fail"}
        </span>
      </button>

      {open && (
        <div
          className="flex flex-col gap-3 border-t px-3 py-3"
          style={{ borderColor: "var(--t-border)" }}
        >
          <CodeBlock label="request" value={call.request} />
          <div className="flex flex-col gap-3 sm:flex-row">
            <CodeBlock label="raw response" value={call.response} />
            <div className="sm:w-44">
              <div
                className="eyebrow mb-1 text-[10px]"
                style={{ color: "var(--t-muted)" }}
              >
                normalized
              </div>
              <EnvelopePill env={env} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
