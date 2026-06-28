"use client";

import { Boxes, Globe, Loader2 } from "lucide-react";
import { type PhaseState } from "@/components/run/PhaseTimeline";

// A tool as it materializes during the build (richer than the final ProxyTool:
// carries the upstream method+path so the stream reads like a compiler mapping).
export type BuildTool = {
  name: string;
  method?: string | null;
  path?: string | null;
  kind?: string;
};

// A single build-log line (the proxy-namespaced `line` events, de-prefixed).
export type BuildLine = { msg: string; ok: boolean };

export type ProxyBuildState = {
  lines: BuildLine[];
  tools: BuildTool[];
  upstream: string | null;
  // The three proxy phases drive the stage labels.
  generate: PhaseState;
  deploy: PhaseState;
  verify: PhaseState;
};

const STAGES: { key: "generate" | "deploy" | "verify"; label: string }[] = [
  { key: "generate", label: "Generate" },
  { key: "deploy", label: "Deploy" },
  { key: "verify", label: "Verify" },
];

function StageDot({ state }: { state: PhaseState }) {
  if (state === "done") {
    return (
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: "var(--success)" }}
      />
    );
  }
  if (state === "active") {
    return (
      <Loader2
        className="h-3 w-3 shrink-0"
        style={{ color: "var(--primary)", animation: "spinner 0.8s linear infinite" }}
        strokeWidth={2}
      />
    );
  }
  return (
    <span
      className="h-1.5 w-1.5 rounded-full"
      style={{ background: "var(--border-strong)" }}
    />
  );
}

export function ProxyBuildStream({ build }: { build: ProxyBuildState }) {
  const activeStage =
    build.verify !== "pending"
      ? "verify"
      : build.deploy !== "pending"
        ? "deploy"
        : "generate";
  const headline =
    activeStage === "verify"
      ? "Verifying the proxy…"
      : activeStage === "deploy"
        ? "Deploying the proxy runtime…"
        : "Building the MCP proxy…";

  return (
    <div
      className="overflow-hidden rounded-lg border"
      style={{
        borderColor: "color-mix(in oklch, var(--primary) 40%, transparent)",
        background: "var(--surface-1)",
      }}
    >
      {/* Header — building state + stage progress */}
      <div
        className="flex flex-wrap items-center gap-2.5 border-b px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <span
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          <Loader2
            className="h-4 w-4"
            style={{ animation: "spinner 0.9s linear infinite" }}
            strokeWidth={2}
          />
        </span>
        <span className="font-display text-[14px] font-semibold">{headline}</span>
        <div className="ml-auto flex items-center gap-3">
          {STAGES.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <StageDot state={build[s.key]} />
              <span
                className="data text-[10px] uppercase tracking-[0.08em]"
                style={{
                  color:
                    build[s.key] === "pending"
                      ? "var(--fg-subtle)"
                      : build[s.key] === "done"
                        ? "var(--success)"
                        : "var(--primary)",
                }}
              >
                {s.label}
              </span>
            </span>
          ))}
        </div>
      </div>

      {/* Upstream chip */}
      {build.upstream && (
        <div
          className="flex items-center gap-2 border-b px-4 py-2.5"
          style={{ borderColor: "var(--border)" }}
        >
          <span className="eyebrow text-[10px]">upstream</span>
          <span
            className="inline-flex min-w-0 items-center gap-1.5 rounded border px-2 py-0.5"
            style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
          >
            <Globe className="h-3 w-3 shrink-0" style={{ color: "var(--primary)" }} />
            <code
              className="min-w-0 truncate font-mono text-[12px]"
              style={{ color: "var(--foreground)" }}
              title={build.upstream}
            >
              {build.upstream}
            </code>
          </span>
        </div>
      )}

      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_300px]">
        {/* Build log — reads like a CI build */}
        <div
          className="border-b p-3 lg:border-b-0 lg:border-r"
          style={{ borderColor: "var(--border)" }}
        >
          <div className="eyebrow mb-2 px-1 text-[10px]">build log</div>
          <div
            className="scrollbar-minimal max-h-72 overflow-auto rounded px-3 py-2.5 font-mono text-[12px] leading-relaxed"
            style={{
              background: "var(--t-bg)",
              border: "1px solid var(--t-border)",
              color: "var(--t-fg)",
            }}
          >
            {build.lines.length === 0 ? (
              <span style={{ color: "var(--muted-foreground)" }}>
                starting build…
              </span>
            ) : (
              build.lines.map((ln, i) => (
                <div key={i} className="flex gap-2">
                  <span
                    className="shrink-0 select-none"
                    style={{ color: ln.ok ? "var(--primary)" : "var(--danger)" }}
                  >
                    {ln.ok ? "›" : "✕"}
                  </span>
                  <span
                    style={{
                      color: ln.ok ? "var(--t-fg)" : "var(--danger)",
                      wordBreak: "break-word",
                    }}
                  >
                    {ln.msg}
                  </span>
                </div>
              ))
            )}
            {/* Blinking caret while still building */}
            {build.verify !== "done" && (
              <span
                className="ml-0.5 inline-block"
                style={{
                  color: "var(--primary)",
                  animation: "cursor-blink 1s steps(1) infinite",
                }}
              >
                ▍
              </span>
            )}
          </div>
        </div>

        {/* Tools materializing one by one */}
        <div className="p-3">
          <div className="eyebrow mb-2 px-1 text-[10px]">
            tools · {build.tools.length}
          </div>
          {build.tools.length === 0 ? (
            <div
              className="rounded px-3 py-2.5 text-[12px]"
              style={{
                background: "var(--surface-2)",
                color: "var(--muted-foreground)",
              }}
            >
              mapping operations…
            </div>
          ) : (
            <ul className="scrollbar-minimal flex max-h-72 flex-col gap-1 overflow-auto">
              {build.tools.map((t) => (
                <li
                  key={t.name}
                  className="flex items-start gap-2 rounded px-2 py-1.5"
                  style={{ background: "var(--surface-2)", animation: "cn-enter 200ms ease-out" }}
                >
                  <Boxes
                    className="mt-0.5 h-3.5 w-3.5 shrink-0"
                    style={{ color: "var(--primary)" }}
                    strokeWidth={1.75}
                  />
                  <div className="min-w-0 flex-1">
                    <code
                      className="block truncate font-mono text-[12px]"
                      style={{ color: "var(--primary)" }}
                    >
                      {t.name}
                    </code>
                    {t.method && t.path && (
                      <code
                        className="block truncate font-mono text-[10px]"
                        style={{ color: "var(--muted-foreground)" }}
                        title={`${t.method} ${t.path}`}
                      >
                        {t.method} {t.path}
                      </code>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
