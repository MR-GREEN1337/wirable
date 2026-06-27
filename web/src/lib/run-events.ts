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
  | { type: "score"; total: number; dimensions: ScoreDimension[] }
  | {
      type: "proxy_ready";
      mcp_url: string;
      tools: ProxyTool[];
      advertise: AdvertiseBundle;
    }
  | { type: "verify"; before: number; after: number; delta: number }
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
