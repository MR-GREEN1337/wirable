// Wirable run-event vocabulary — mirrors backend/src/core/contracts.py.
// See wirable/CONTRACTS.md §(a). The frontend codes to these shapes exactly.

export type PhaseName =
  | "recon"
  | "test"
  | "score"
  | "generate"
  | "deploy"
  | "verify";

export type PhaseStatus = "start" | "done";

export type ClassifyKind = "api" | "site";

export type NormalizedEnvelope = {
  success: boolean;
  error_code: string | null;
  retryable: boolean;
};

export type WorkflowName =
  | "signup"
  | "core_action"
  | "error_handling"
  | "retry_idempotency";

export type ScoreDimension = {
  dim: string;
  passed: boolean;
  evidence: string;
};

export type CardTone = "good" | "bad" | "warn";

// A "Wrapped"-style insight card — the narrative payload of the report.
export type WrappedCardData = {
  eyebrow: string; // a short question
  headline: string; // bold verdict, ≤5 words
  detail: string; // one specific evidence sentence
  dimension: string; // api_surface | auth | ... | general
  tone: CardTone;
};

export type AdvertiseBundle = {
  well_known: Record<string, unknown>;
  llms_txt: string;
  link_tag: string;
  header: string;
};

export type ProxyTool = {
  name: string;
  description: string;
};

// Discriminated union over every SSE event `type`.
export type RunEvent =
  | { type: "phase"; phase: PhaseName; status: PhaseStatus }
  | { type: "classify"; kind: ClassifyKind; evidence: string }
  | { type: "line"; ok: boolean; msg: string; dim?: string }
  | {
      type: "screenshot";
      seq: number;
      // Which parallel CATTS agent produced this frame (0..N-1). Absent on
      // legacy single-agent events → treated as agent 0. seqs are per-agent, so
      // consumers must key by `${agent}:${seq}` to avoid cross-agent collisions.
      agent?: number;
      caption: string;
      dimension?: string;
      image: string;
    }
  | {
      type: "tool_call";
      name: string;
      request: unknown;
      response: unknown;
      normalized: NormalizedEnvelope;
    }
  | {
      type: "workflow_result";
      workflow: WorkflowName;
      passed: boolean;
      evidence: string;
    }
  | { type: "cards"; cards: WrappedCardData[] }
  | {
      type: "score";
      total: number;
      dimensions: ScoreDimension[];
      cards?: WrappedCardData[];
    }
  // Emitted once per tool as the generator maps it during proxy build, so the
  // build stream can materialize the toolset one row at a time.
  | {
      type: "proxy_tool";
      name: string;
      method?: string | null;
      path?: string | null;
      kind?: string;
    }
  | {
      type: "proxy_ready";
      mcp_url: string;
      tools: ProxyTool[];
      advertise: AdvertiseBundle;
    }
  | { type: "verify"; before: number; after: number; delta: number }
  | {
      type: "fix_pr";
      pr_url: string;
      files: string[];
      branch?: string | null;
      repo?: string | null;
      // Unified diff of the agent-ready changes the PR contains (capped).
      // Omitted for the REST file-drop fallback.
      diff?: string | null;
      error?: string | null;
    }
  // The agent paused mid-run and needs a value from the human (e.g. an OTP).
  // Cleared on the resume `line` ("human input received…") or on done/error.
  | { type: "needs_input"; prompt: string; kind?: string; request_id: string }
  | { type: "done" }
  | { type: "error"; msg: string };

export const PHASES: { key: PhaseName; label: string }[] = [
  { key: "recon", label: "Recon" },
  { key: "test", label: "Test" },
  { key: "score", label: "Score" },
  { key: "generate", label: "Generate" },
  { key: "deploy", label: "Deploy" },
  { key: "verify", label: "Verify" },
];

export const WORKFLOW_LABELS: Record<WorkflowName, string> = {
  signup: "Signup",
  core_action: "Core action",
  error_handling: "Error handling",
  retry_idempotency: "Retry / idempotency",
};

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export function scoreColor(n: number): string {
  if (n >= 70) return "var(--success)";
  if (n >= 50) return "var(--warning)";
  return "var(--danger)";
}

export function scoreLabel(n: number): string {
  if (n >= 70) return "agent-ready";
  if (n >= 50) return "partial";
  return "blocked";
}
