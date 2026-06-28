"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { NormalizedEnvelope } from "@/lib/run-events";
import { toolConcept } from "@/lib/run-icons";

export type ToolCall = {
  name: string;
  request: unknown;
  response: unknown;
  normalized: NormalizedEnvelope;
};

const MAX_CHARS = 2000;

// Detect a raw HTML document/fragment so we summarize instead of dumping it.
function looksLikeHtml(s: string): boolean {
  const head = s.trimStart().slice(0, 200).toLowerCase();
  return (
    head.startsWith("<!doctype") ||
    head.startsWith("<html") ||
    (/<\/?[a-z][\s\S]*>/i.test(s) && s.includes("</"))
  );
}

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
  const raw = pretty(value);
  const isHtml = typeof value === "string" && looksLikeHtml(value);
  const body = isHtml
    ? `[HTML response · ${value.length.toLocaleString()} chars hidden]`
    : raw.length > MAX_CHARS
      ? `${raw.slice(0, MAX_CHARS)}\n… (${(raw.length - MAX_CHARS).toLocaleString()} more chars)`
      : raw;

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
          color: isHtml ? "var(--t-muted)" : "var(--t-fg)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}
      >
        {body}
      </pre>
    </div>
  );
}

/* ── Normalized envelope — three labeled pills ────────────────────────────────*/

function Pill({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div
      className="flex items-center justify-between gap-2 rounded px-2 py-1"
      style={{ background: "var(--t-bg)", border: "1px solid var(--t-border)" }}
    >
      <span className="text-[9px] uppercase tracking-[0.08em]" style={{ color: "var(--t-muted)" }}>
        {label}
      </span>
      <span className="data text-[11px] font-semibold" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

function Envelope({ env }: { env: NormalizedEnvelope }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="eyebrow text-[10px]" style={{ color: "var(--t-muted)" }}>
        normalized envelope
      </div>
      <Pill
        label="success"
        value={env.success ? "true" : "false"}
        color={env.success ? "var(--t-green)" : "var(--t-red)"}
      />
      <Pill
        label="error_code"
        value={env.error_code ?? "null"}
        color={env.error_code ? "var(--t-amber)" : "var(--t-muted)"}
      />
      <Pill
        label="retryable"
        value={env.retryable ? "true" : "false"}
        color={env.retryable ? "var(--t-amber)" : "var(--t-muted)"}
      />
    </div>
  );
}

export function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const env = call.normalized;
  const concept = toolConcept(call.name);
  const Icon = concept.icon;
  const statusColor = env.success ? "var(--t-green)" : "var(--t-red)";

  return (
    <div
      className="overflow-hidden rounded-md border font-mono transition-colors duration-[80ms]"
      style={{
        borderColor: open ? "var(--t-border)" : "var(--t-border)",
        background: "var(--t-s1)",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left"
      >
        <ChevronRight
          className="h-3.5 w-3.5 shrink-0 transition-transform duration-200"
          style={{
            color: "var(--t-muted)",
            transform: open ? "rotate(90deg)" : "none",
          }}
        />
        <span
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded"
          style={{
            color: concept.accent,
            border: `1px solid ${concept.accent}`,
            background: "var(--t-bg)",
          }}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--t-fg)" }}>
          {call.name}
        </span>
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: statusColor }} />
        <span
          className="data shrink-0 text-[10px] uppercase tracking-[0.08em]"
          style={{ color: statusColor }}
        >
          {env.success ? "ok" : env.error_code ?? "fail"}
        </span>
      </button>

      <div
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <div
            className="flex flex-col gap-3 border-t px-3 py-3"
            style={{ borderColor: "var(--t-border)" }}
          >
            <CodeBlock label="request" value={call.request} />
            <div className="flex flex-col gap-3 sm:flex-row">
              <CodeBlock label="raw response" value={call.response} />
              <div className="sm:w-48">
                <Envelope env={env} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
