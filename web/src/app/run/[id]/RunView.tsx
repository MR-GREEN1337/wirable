"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, AlertTriangle, ChevronRight, Wrench } from "lucide-react";
import { type TerminalLine, type AuditShot } from "@/components/AuditTerminal";
import { AgentGrid } from "@/components/run/AgentGrid";
import { FixPrRow, type FixPr } from "@/components/run/FixPrRow";
import { FixWithGithub } from "@/components/run/FixWithGithub";
import { useRunPoll } from "@/lib/use-run-poll";
import { useSession } from "next-auth/react";
import { useAccess, beginCheckout } from "@/components/AccessGate";
import { ActivityStream } from "@/components/run/ActivityStream";
import { PhaseTimeline, type PhaseState } from "@/components/run/PhaseTimeline";
import { ToolCallCard, type ToolCall } from "@/components/run/ToolCallCard";
import { ScorePanel } from "@/components/run/ScorePanel";
import { WrappedCards } from "@/components/run/WrappedCards";
import { WorkflowResults, type WorkflowResult } from "@/components/run/WorkflowResults";
import { ProxyPanel } from "@/components/run/ProxyPanel";
import {
  ProxyBuildStream,
  type ProxyBuildState,
  type BuildTool,
} from "@/components/run/ProxyBuildStream";
import { VerifyPanel } from "@/components/run/VerifyPanel";
import { AuthModal, type AuthPayload } from "@/components/run/AuthModal";
import { HumanInputPrompt, type PendingInput } from "@/components/run/HumanInputPrompt";
import { Favicon, LivePulse } from "@/components/run/Favicon";
import { Reveal } from "@/components/Reveal";
import { CtaButton } from "@/components/CtaButton";
import { GlassShaderLazy } from "@/components/global/GlassShaderLazy";
import {
  BACKEND_URL,
  PHASES,
  type RunEvent,
  type PhaseName,
  type ClassifyKind,
  type ScoreDimension,
  type AdvertiseBundle,
  type ProxyTool,
  type WrappedCardData,
} from "@/lib/run-events";
import { track } from "@/components/global/Analytics";

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
  cards: WrappedCardData[];
  proxy: ProxyReady | null;
  // Live "building the MCP proxy" stream — the proxy-namespaced build log,
  // tools materializing one by one, and the discovered upstream.
  proxyBuild: ProxyBuildState;
  verify: { before: number; after: number; delta: number } | null;
  fixPr: FixPr | null;
  // The agent paused mid-run and is waiting on a value from the human.
  pendingInput: PendingInput | null;
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
  cards: [],
  proxy: null,
  proxyBuild: {
    lines: [],
    tools: [],
    upstream: null,
    generate: "pending",
    deploy: "pending",
    verify: "pending",
  },
  verify: null,
  fixPr: null,
  pendingInput: null,
  finished: null,
  errorMsg: null,
};

function reducer(state: State, ev: RunEvent): State {
  switch (ev.type) {
    case "phase": {
      const phases = { ...state.phases };
      phases[ev.phase] = ev.status === "done" ? "done" : "active";
      // Mirror the proxy phases into the build-stream slice so its stage row
      // (Generate / Deploy / Verify) lights up live.
      let proxyBuild = state.proxyBuild;
      if (ev.phase === "generate" || ev.phase === "deploy" || ev.phase === "verify") {
        proxyBuild = {
          ...proxyBuild,
          [ev.phase]: ev.status === "done" ? "done" : "active",
        };
      }
      return { ...state, phases, proxyBuild };
    }
    case "classify":
      return { ...state, classify: { kind: ev.kind, evidence: ev.evidence } };
    case "line": {
      // Proxy-namespaced lines feed the dedicated build stream, not the run's
      // Activity log (keeps Activity focused on the test run).
      if (ev.msg.startsWith("proxy: ")) {
        const msg = ev.msg.slice("proxy: ".length);
        const prev = state.proxyBuild.lines[state.proxyBuild.lines.length - 1];
        if (prev && prev.msg === msg && prev.ok === ev.ok) return state;
        // "upstream: https://api.kortix.com" pins the upstream chip.
        let upstream = state.proxyBuild.upstream;
        const m = msg.match(/^upstream:\s*(\S+)/);
        if (m) upstream = m[1];
        return {
          ...state,
          proxyBuild: {
            ...state.proxyBuild,
            upstream,
            lines: [...state.proxyBuild.lines, { msg, ok: ev.ok }],
          },
        };
      }
      // The resume signal dismisses any pending human-input card.
      const pendingInput = ev.msg.includes("human input received")
        ? null
        : state.pendingInput;
      const prev = state.lines[state.lines.length - 1];
      if (prev && prev.msg === ev.msg && prev.dim === ev.dim) {
        return pendingInput === state.pendingInput ? state : { ...state, pendingInput };
      }
      return {
        ...state,
        pendingInput,
        lines: [...state.lines, { type: ev.ok ? "ok" : "err", msg: ev.msg, dim: ev.dim }],
      };
    }
    case "proxy_tool": {
      // Materialize a tool row as the generator maps it (idempotent by name —
      // the poll replays history from cursor=0 each epoch).
      if (state.proxyBuild.tools.some((t) => t.name === ev.name)) return state;
      const tool: BuildTool = {
        name: ev.name,
        method: ev.method,
        path: ev.path,
        kind: ev.kind,
      };
      return {
        ...state,
        proxyBuild: { ...state.proxyBuild, tools: [...state.proxyBuild.tools, tool] },
      };
    }
    case "needs_input":
      return {
        ...state,
        pendingInput: {
          prompt: ev.prompt,
          kind: ev.kind,
          request_id: ev.request_id,
        },
      };
    case "screenshot": {
      // seqs are per-agent (each sandbox numbers its own frames from 0), so a
      // bare seq collides across agents. De-dupe by the namespaced `${agent}:${seq}`
      // key; the poll loop replays history from cursor=0 each epoch, so this must
      // be idempotent.
      const agent = ev.agent ?? 0;
      if (state.shots.some((s) => (s.agent ?? 0) === agent && s.seq === ev.seq))
        return state;
      return {
        ...state,
        shots: [
          ...state.shots,
          {
            seq: ev.seq,
            agent,
            caption: ev.caption,
            dimension: ev.dimension,
            image: ev.image,
          },
        ],
      };
    }
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
    case "cards":
      return { ...state, cards: ev.cards ?? [] };
    case "score": {
      const phases = { ...state.phases, score: "done" as PhaseState };
      // Cards may arrive standalone (cards event) or ride on the score event.
      const cards = state.cards.length ? state.cards : ev.cards ?? [];
      return {
        ...state,
        score: { total: ev.total, dimensions: ev.dimensions },
        cards,
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
      return { ...state, verify: { before: ev.before, after: ev.after, delta: ev.delta }, phases };
    }
    case "fix_pr":
      return {
        ...state,
        fixPr: {
          pr_url: ev.pr_url,
          files: ev.files ?? [],
          branch: ev.branch,
          repo: ev.repo,
          error: ev.error,
        },
      };
    case "done":
      return { ...state, finished: "done", pendingInput: null };
    case "error":
      return { ...state, finished: "error", errorMsg: ev.msg, pendingInput: null };
    default:
      return state;
  }
}

// ── Status pill ──────────────────────────────────────────────────────────────
function StatusPill({ finished }: { finished: "done" | "error" | null }) {
  const [label, color] =
    finished === "error"
      ? (["failed", "var(--danger)"] as const)
      : finished === "done"
        ? (["complete", "var(--success)"] as const)
        : (["running", "var(--primary)"] as const);
  return (
    <span
      className="data inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-[0.08em]"
      style={{ border: `1px solid ${color}`, color }}
    >
      {finished === null ? (
        <LivePulse color={color} size={6} />
      ) : (
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      )}
      {label}
    </span>
  );
}

// ── Section wrapper ──────────────────────────────────────────────────────────
function Section({
  title,
  children,
  right,
  delay = 0,
}: {
  title: string;
  children: React.ReactNode;
  right?: React.ReactNode;
  delay?: number;
}) {
  return (
    <Reveal delay={delay}>
      <section className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between">
          <h2 className="eyebrow">{title}</h2>
          {right}
        </div>
        {children}
      </section>
    </Reveal>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────
export function RunView({ runId, domain }: { runId: string; domain: string }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const { status: accessStatus } = useAccess();
  const { data: sessionData } = useSession();
  const isPro = !!accessStatus?.unlimited; // paid/unlimited; the fix is Pro-gated
  const proToken = sessionData?.backendToken;
  const [connError, setConnError] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [proxySubmitting, setProxySubmitting] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);
  // Bumping this remounts the poll loop (with a fresh cursor=0 replay) — used
  // after the proxy FIX flow kicks off so its new events stream in live too.
  const [pollEpoch, setPollEpoch] = useState(0);
  // Set when "Fix with GitHub" kicks the harness — drives the running state until
  // the fix_pr event lands (cleared implicitly: FixWithGithub hides once fixPr).
  const [fixStarted, setFixStarted] = useState(false);

  // Poll the SSE-free state endpoint every ~700ms and replay events as if they
  // arrived over a stream. This survives the Next standalone proxy's buffering,
  // so frames play live (see useRunPoll). Each epoch replays full history from
  // cursor=0; the reducer is idempotent (dedupes by seq / phase), so bumping
  // pollEpoch after proxy-start re-applies what we have, then streams the new
  // fix_pr / verify / done events on the same bus.
  useRunPoll(runId, (ev) => dispatch(ev), {
    onError: () => setConnError(true),
    epoch: pollEpoch,
  });

  // Launch-funnel analytics. The poll loop replays full history each epoch and
  // the reducer is idempotent, so guard each event with a ref to fire it once.
  const scoreTracked = useRef(false);
  const proxyTracked = useRef(false);
  useEffect(() => {
    if (state.score && !scoreTracked.current) {
      scoreTracked.current = true;
      track("score_received", { total: state.score.total });
    }
  }, [state.score]);
  useEffect(() => {
    if (state.proxy && !proxyTracked.current) {
      proxyTracked.current = true;
      track("proxy_generated");
    }
  }, [state.proxy]);

  async function startProxy(auth: AuthPayload) {
    setProxySubmitting(true);
    setProxyError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/run/${runId}/proxy`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Hosting the proxy is auth'd + Pro-gated — send the bearer token
          // (the endpoint 401s without it, which surfaced as a generic error).
          ...(proToken ? { Authorization: `Bearer ${proToken}` } : {}),
        },
        body: JSON.stringify({ auth }),
      });
      if (res.status === 401) {
        setProxyError("Sign in to host the MCP proxy.");
        return;
      }
      if (res.status === 402) {
        setProxyError("Upgrade to Pro to host the MCP proxy.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // The base poll loop stopped at the run's terminal `done`. Spin up a fresh
      // poll session to stream the proxy/fix_pr/verify events that follow.
      setPollEpoch((n) => n + 1);
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
  // Only offer the proxy gate once the agent has fully FINISHED the run (not the
  // instant the score event lands, which is a beat before `done`) — otherwise the
  // "generate a proxy" panel flashes while the agent is still working.
  const showProxyGate =
    !running && !!state.score && !state.proxy && state.phases.generate === "pending";
  const hasActivity =
    state.lines.length > 0 || state.shots.length > 0 || state.toolCalls.length > 0;

  return (
    <div
      className="relative min-h-screen"
      style={{ background: "var(--background)", color: "var(--foreground)" }}
    >
      {/* A whisper of the signature bloom anchored at the very top — far dimmer
          than the landing so it never fights the run data. Sits behind the
          header, fades out before the content rail. Lazy/reduced-motion-aware. */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 z-0 h-[320px] overflow-hidden opacity-[0.14]"
        aria-hidden
        style={{
          maskImage:
            "linear-gradient(to bottom, black 0%, black 30%, transparent 100%)",
          WebkitMaskImage:
            "linear-gradient(to bottom, black 0%, black 30%, transparent 100%)",
        }}
      >
        <GlassShaderLazy />
      </div>

      {/* Sticky compact header */}
      <header
        className="sticky top-0 z-30 border-b"
        style={{
          background: "color-mix(in oklch, var(--surface-1) 85%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-2.5 px-6">
          <Link
            href="/dashboard"
            className="cn-hover inline-flex items-center gap-1.5 text-[13px]"
            style={{ color: "var(--muted-foreground)" }}
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Dashboard
          </Link>
          <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--fg-subtle)" }} />
          <span className="eyebrow shrink-0">Run</span>
          <div className="ml-1 flex min-w-0 items-center gap-2">
            <Favicon domain={domain} size={16} />
            <span
              className="data min-w-0 truncate text-[13px] font-medium"
              style={{ color: "var(--foreground)" }}
              title={domain || runId}
            >
              {domain || runId}
            </span>
          </div>
          <span className="ml-auto shrink-0">
            <StatusPill finished={state.finished} />
          </span>
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1320px] gap-10 px-6 py-8 sm:px-10 lg:grid-cols-[280px_minmax(0,1fr)]">
        {/* LEFT rail — pipeline + counts */}
        <aside className="flex flex-col gap-4 lg:sticky lg:top-[78px] lg:self-start">
          <Reveal>
            <PhaseTimeline
              states={state.phases}
              classify={state.classify}
              proxyUnlocked={proxyUnlocked}
            />
          </Reveal>
          {hasActivity && (
            <Reveal delay={40}>
              <div
                className="grid grid-cols-3 divide-x rounded-md border text-center"
                style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
              >
                {[
                  ["lines", state.lines.length],
                  ["frames", state.shots.length],
                  ["tools", state.toolCalls.length],
                ].map(([label, n]) => (
                  <div key={label} className="flex flex-col gap-1 px-2 py-3" style={{ borderColor: "var(--border)" }}>
                    <span className="data text-[19px] font-semibold leading-none">{n}</span>
                    <span className="eyebrow text-[9px]">{label}</span>
                  </div>
                ))}
              </div>
            </Reveal>
          )}
        </aside>

        {/* RIGHT — the live story */}
        <div className="flex min-w-0 flex-col gap-8">
          {state.finished === "error" && (
            <div
              className="flex items-start gap-2 rounded-md border px-3 py-2.5 text-[13px]"
              style={{
                borderColor: "color-mix(in oklch, var(--danger) 35%, transparent)",
                background: "color-mix(in oklch, var(--danger) 6%, transparent)",
                color: "var(--danger)",
              }}
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{state.errorMsg || "The run failed."}</span>
            </div>
          )}
          {connError && !state.finished && state.lines.length === 0 && (
            <div
              className="flex items-center gap-2 rounded-md border px-3 py-2.5 text-[13px]"
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              <LivePulse size={6} />
              Connecting to the run stream…
            </div>
          )}

          {/* All N agents browsing side by side — the consensus, live. */}
          <Section title="Live agents">
            <AgentGrid domain={domain} shots={state.shots} live={running} />
          </Section>

          {/* Mid-run pause: the agent needs a value from the human (e.g. an OTP).
              Anchored right under the viewport so it's impossible to miss. */}
          {state.pendingInput && (
            <HumanInputPrompt runId={runId} pending={state.pendingInput} />
          )}

          {/* Activity log — also live while the GitHub fix agent is working
              (it streams its steps here after the audit's terminal `done`). */}
          <Section title="Activity">
            <ActivityStream
              lines={state.lines}
              running={running || (fixStarted && !state.fixPr)}
            />
          </Section>

          {/* Tool calls */}
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

          {/* The headline of the report — Wrapped-style insight cards */}
          {state.cards.length > 0 && (
            <Section title="The verdict">
              <WrappedCards cards={state.cards} />
            </Section>
          )}

          {/* Score — now one supporting element beneath the narrative */}
          {state.score && (
            <Section title="Score">
              <ScorePanel total={state.score.total} dimensions={state.score.dimensions} />
            </Section>
          )}

          {/* Proxy gate */}
          {showProxyGate && (
            <Reveal>
              <div
                className="rounded-lg border p-5"
                style={{
                  borderColor: "color-mix(in oklch, var(--primary) 40%, transparent)",
                  background: "var(--primary-soft)",
                }}
              >
                <div className="flex items-start gap-3">
                  <span
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md"
                    style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
                  >
                    <Wrench className="h-4.5 w-4.5" strokeWidth={1.75} style={{ width: 18, height: 18 }} />
                  </span>
                  <div className="flex-1">
                    <h3 className="font-display text-[16px] font-semibold">
                      Make it agent-ready: generate a proxy
                    </h3>
                    <p
                      className="mt-1 text-[13px] leading-relaxed"
                      style={{ color: "var(--muted-foreground)" }}
                    >
                      We host an MCP server in front of {domain || "the target"} that fixes
                      the semantic breakage, with no code changes on your side. Configure how
                      agents should authenticate to begin.
                    </p>
                    {proxyError && (
                      <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
                        {proxyError}
                      </p>
                    )}
                    <div className="mt-4">
                      {isPro ? (
                        <CtaButton onClick={() => setAuthOpen(true)} size="sm">
                          Generate proxy (no code)
                        </CtaButton>
                      ) : (
                        <div className="flex flex-col gap-1.5">
                          <CtaButton onClick={() => proToken && beginCheckout(proToken)} size="sm">
                            Upgrade to Pro to host the proxy
                          </CtaButton>
                          <span className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
                            The audit is free. Hosting the MCP proxy that fixes it is Pro.
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </Reveal>
          )}

          {/* Building state — the live MCP-proxy builder stream */}
          {proxyUnlocked && !state.proxy && (
            <Section title="Building the MCP proxy">
              <ProxyBuildStream build={state.proxyBuild} />
            </Section>
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

          {/* Fix with GitHub — kicks the harness that clones the repo + opens a
              PR. Hidden until there's a score (something to fix); auto-hides once
              the fix_pr result arrives (FixPrRow renders it below). */}
          {!running && state.score && (
            <Reveal>
              <FixWithGithub
                runId={runId}
                domain={domain}
                dimensions={state.score?.dimensions}
                score={state.score?.total}
                running={fixStarted && !state.fixPr}
                hasResult={!!state.fixPr}
                onStarted={() => {
                  setFixStarted(true);
                  setPollEpoch((n) => n + 1);
                }}
              />
            </Reveal>
          )}

          {/* Fix PR — the FIX flow opened a PR on the connected repo */}
          {state.fixPr && (
            <Section title="Fix PR">
              <FixPrRow pr={state.fixPr} />
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
