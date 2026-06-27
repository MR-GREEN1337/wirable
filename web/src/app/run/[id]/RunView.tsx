"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Sparkles, AlertTriangle } from "lucide-react";
import { AuditTerminal, type TerminalLine, type AuditShot } from "@/components/AuditTerminal";
import { PhaseTimeline, type PhaseState } from "@/components/run/PhaseTimeline";
import { ToolCallCard, type ToolCall } from "@/components/run/ToolCallCard";
import { ScorePanel } from "@/components/run/ScorePanel";
import { WorkflowResults, type WorkflowResult } from "@/components/run/WorkflowResults";
import { ProxyPanel } from "@/components/run/ProxyPanel";
import { VerifyPanel } from "@/components/run/VerifyPanel";
import { AuthModal, type AuthPayload } from "@/components/run/AuthModal";
import { CtaButton } from "@/components/CtaButton";
import {
  BACKEND_URL,
  PHASES,
  type RunEvent,
  type PhaseName,
  type ClassifyKind,
  type ScoreDimension,
  type AdvertiseBundle,
  type ProxyTool,
} from "@/lib/run-events";

// ── State ──────────────────────────────────────────────────────────────────
type ProxyReady = {
  mcp_url: string;
  tools: ProxyTool[];
  advertise: AdvertiseBundle;
};

type State = {
  phases: Record<PhaseName, PhaseState>;
  classify: { kind: ClassifyKind; evidence: string } | null;
  lines: TerminalLine[];
  shots: AuditShot[];
  toolCalls: ToolCall[];
  workflows: WorkflowResult[];
  score: { total: number; dimensions: ScoreDimension[] } | null;
  proxy: ProxyReady | null;
  verify: { before: number; after: number; delta: number } | null;
  finished: "done" | "error" | null;
  errorMsg: string | null;
};

const INITIAL_PHASES = PHASES.reduce(
  (acc, p) => ({ ...acc, [p.key]: "pending" as PhaseState }),
  {} as Record<PhaseName, PhaseState>
);

const initialState: State = {
  phases: { ...INITIAL_PHASES },
  classify: null,
  lines: [],
  shots: [],
  toolCalls: [],
  workflows: [],
  score: null,
  proxy: null,
  verify: null,
  finished: null,
  errorMsg: null,
};

function reducer(state: State, ev: RunEvent): State {
  switch (ev.type) {
    case "phase": {
      const phases = { ...state.phases };
      phases[ev.phase] = ev.status === "done" ? "done" : "active";
      return { ...state, phases };
    }
    case "classify":
      return { ...state, classify: { kind: ev.kind, evidence: ev.evidence } };
    case "line":
      return {
        ...state,
        lines: [
          ...state.lines,
          { type: ev.ok ? "ok" : "err", msg: ev.msg, dim: ev.dim },
        ],
      };
    case "screenshot":
      if (state.shots.some((s) => s.seq === ev.seq)) return state;
      return {
        ...state,
        shots: [
          ...state.shots,
          {
            seq: ev.seq,
            caption: ev.caption,
            dimension: ev.dimension,
            image: ev.image,
          },
        ],
      };
    case "tool_call":
      return {
        ...state,
        toolCalls: [
          ...state.toolCalls,
          {
            name: ev.name,
            request: ev.request,
            response: ev.response,
            normalized: ev.normalized,
          },
        ],
      };
    case "workflow_result":
      return {
        ...state,
        workflows: [
          ...state.workflows.filter((w) => w.workflow !== ev.workflow),
          { workflow: ev.workflow, passed: ev.passed, evidence: ev.evidence },
        ],
      };
    case "score": {
      const phases = { ...state.phases, score: "done" as PhaseState };
      return {
        ...state,
        score: { total: ev.total, dimensions: ev.dimensions },
        phases,
      };
    }
    case "proxy_ready": {
      const phases = { ...state.phases };
      phases.generate = "done";
      phases.deploy = "done";
      return {
        ...state,
        proxy: { mcp_url: ev.mcp_url, tools: ev.tools, advertise: ev.advertise },
        phases,
      };
    }
    case "verify": {
      const phases = { ...state.phases, verify: "done" as PhaseState };
      return {
        ...state,
        verify: { before: ev.before, after: ev.after, delta: ev.delta },
        phases,
      };
    }
    case "done":
      return { ...state, finished: "done" };
    case "error":
      return { ...state, finished: "error", errorMsg: ev.msg };
    default:
      return state;
  }
}

// ── Section wrapper ──────────────────────────────────────────────────────────
function Section({
  title,
  children,
  right,
}: {
  title: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="eyebrow">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────
export function RunView({ runId, domain }: { runId: string; domain: string }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [connError, setConnError] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [proxySubmitting, setProxySubmitting] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  // Open the stream once. Backend replays history, so late subscribers are safe.
  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(`${BACKEND_URL}/api/v1/run/${runId}/stream`);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as RunEvent;
        dispatch(ev);
        if (ev.type === "done" || ev.type === "error") es.close();
      } catch {
        /* skip malformed event */
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects; only flag if the run never produced data.
      setConnError(true);
    };
    return () => es.close();
  }, [runId]);

  async function startProxy(auth: AuthPayload) {
    setProxySubmitting(true);
    setProxyError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/run/${runId}/proxy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auth }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Re-open the stream so generate/deploy/verify events resume rendering
      // (the backend emits them on the same run bus). If it's already open,
      // the replay-safe reducer dedupes prior events.
      if (esRef.current?.readyState === EventSource.CLOSED) {
        const es = new EventSource(`${BACKEND_URL}/api/v1/run/${runId}/stream`);
        esRef.current = es;
        es.onmessage = (e) => {
          try {
            const ev = JSON.parse(e.data) as RunEvent;
            dispatch(ev);
            if (ev.type === "done" || ev.type === "error") es.close();
          } catch {
            /* skip */
          }
        };
      }
      setAuthOpen(false);
    } catch {
      setProxyError("Could not start proxy generation. Try again.");
    } finally {
      setProxySubmitting(false);
    }
  }

  const proxyUnlocked =
    state.phases.generate !== "pending" ||
    state.phases.deploy !== "pending" ||
    !!state.proxy ||
    proxySubmitting;

  const running = !state.finished;
  const showProxyGate = !!state.score && !state.proxy && state.phases.generate === "pending";

  return (
    <div
      className="min-h-screen"
      style={{ background: "var(--background)", color: "var(--foreground)" }}
    >
      {/* Top bar */}
      <header
        className="sticky top-0 z-30 border-b"
        style={{
          background: "color-mix(in oklch, var(--surface-1) 85%, transparent)",
          backdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-12 max-w-[1180px] items-center gap-4 px-6">
          <Link
            href="/dashboard"
            className="cn-hover inline-flex items-center gap-1.5 text-[13px]"
            style={{ color: "var(--muted-foreground)" }}
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Dashboard
          </Link>
          <span className="data text-[13px]" style={{ color: "var(--foreground)" }}>
            {domain || runId}
          </span>
          {running && (
            <span
              className="data ml-auto text-[10px] uppercase tracking-[0.08em]"
              style={{
                color: "var(--primary)",
                animation: "cursor-blink 1.2s ease-in-out infinite",
              }}
            >
              live
            </span>
          )}
          {state.finished === "done" && (
            <span
              className="data ml-auto text-[10px] uppercase tracking-[0.08em]"
              style={{ color: "var(--success)" }}
            >
              complete
            </span>
          )}
          {state.finished === "error" && (
            <span
              className="data ml-auto text-[10px] uppercase tracking-[0.08em]"
              style={{ color: "var(--danger)" }}
            >
              error
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto grid max-w-[1180px] gap-6 px-6 py-8 lg:grid-cols-[280px_1fr]">
        {/* LEFT rail — phases */}
        <aside className="flex flex-col gap-4 lg:sticky lg:top-[72px] lg:self-start">
          <PhaseTimeline
            states={state.phases}
            classify={state.classify}
            proxyUnlocked={proxyUnlocked}
          />
          {state.score && (
            <div
              className="rounded border px-3 py-2 text-[12px]"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              <span className="eyebrow mr-1.5 text-[10px]">checks</span>
              {state.lines.length} lines · {state.shots.length} frames ·{" "}
              {state.toolCalls.length} tool calls
            </div>
          )}
        </aside>

        {/* RIGHT — the live story */}
        <div className="flex min-w-0 flex-col gap-8">
          {/* Connection / error banners */}
          {state.finished === "error" && (
            <div
              className="flex items-start gap-2 rounded border px-3 py-2.5 text-[13px]"
              style={{
                borderColor: "oklch(0.53 0.22 20 / 0.35)",
                background: "oklch(0.53 0.22 20 / 0.06)",
                color: "var(--danger)",
              }}
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{state.errorMsg || "The run failed."}</span>
            </div>
          )}
          {connError && !state.finished && state.lines.length === 0 && (
            <div
              className="rounded border px-3 py-2.5 text-[13px]"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              Connecting to the run stream… (reconnecting)
            </div>
          )}

          {/* Live browser stage + step log — the hero visual */}
          <Section title="Live agent">
            <AuditTerminal
              domain={domain}
              lines={state.lines}
              score={undefined}
              screenshots={state.shots}
              className="w-full"
            />
          </Section>

          {/* Tool calls — rich envelope view */}
          {state.toolCalls.length > 0 && (
            <Section title={`Tool calls · ${state.toolCalls.length}`}>
              <div className="flex flex-col gap-1.5">
                {state.toolCalls.map((c, i) => (
                  <ToolCallCard key={i} call={c} />
                ))}
              </div>
            </Section>
          )}

          {/* Workflow results */}
          {state.workflows.length > 0 && (
            <Section title="Workflows">
              <WorkflowResults results={state.workflows} />
            </Section>
          )}

          {/* Score */}
          {state.score && (
            <Section title="Score">
              <ScorePanel
                total={state.score.total}
                dimensions={state.score.dimensions}
              />
            </Section>
          )}

          {/* Proxy gate */}
          {showProxyGate && (
            <div
              className="rounded-lg border p-5"
              style={{
                borderColor: "oklch(0.65 0.16 240 / 0.4)",
                background: "var(--primary-soft)",
              }}
            >
              <div className="flex items-start gap-3">
                <Sparkles
                  className="mt-0.5 h-5 w-5 shrink-0"
                  style={{ color: "var(--primary)" }}
                />
                <div className="flex-1">
                  <h3 className="font-display text-[16px] font-semibold">
                    Make it agent-ready — generate a proxy
                  </h3>
                  <p
                    className="mt-1 text-[13px] leading-relaxed"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    We host an MCP server in front of {domain || "the target"} that
                    fixes the semantic breakage — no code changes on your side.
                    Configure how agents should authenticate to begin.
                  </p>
                  {proxyError && (
                    <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
                      {proxyError}
                    </p>
                  )}
                  <div className="mt-4">
                    <CtaButton onClick={() => setAuthOpen(true)} size="sm">
                      Generate proxy (no code)
                    </CtaButton>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Generating spinner state */}
          {proxyUnlocked && !state.proxy && (
            <div
              className="flex items-center gap-3 rounded border px-4 py-3 text-[13px]"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              <span
                className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
                style={{ animation: "spinner 0.8s linear infinite" }}
              />
              Generating &amp; deploying the proxy…
            </div>
          )}

          {/* Proxy ready */}
          {state.proxy && (
            <Section title="Proxy">
              <ProxyPanel
                proxyId={runId}
                mcpUrl={state.proxy.mcp_url}
                tools={state.proxy.tools}
                advertise={state.proxy.advertise}
              />
            </Section>
          )}

          {/* Verify */}
          {state.verify && (
            <Section title="Verify">
              <VerifyPanel
                before={state.verify.before}
                after={state.verify.after}
                delta={state.verify.delta}
              />
            </Section>
          )}
        </div>
      </main>

      <AuthModal
        open={authOpen}
        submitting={proxySubmitting}
        onClose={() => setAuthOpen(false)}
        onSubmit={startProxy}
      />
    </div>
  );
}
