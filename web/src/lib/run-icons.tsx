// Shared concept → {icon, accent} map for the run page.
// One place so every renderer (probe rows, classify chips, tool cards,
// dimension rows, MCP badges) speaks the same visual language.

import {
  Plug,
  Globe,
  Server,
  Lock,
  TriangleAlert,
  Repeat,
  Boxes,
  BookOpen,
  ArrowLeftRight,
  Search,
  Terminal,
  Cpu,
  FileJson,
  Network,
  type LucideIcon,
} from "lucide-react";

export type Concept = {
  icon: LucideIcon;
  /** A CSS color token (var or literal) used for the icon + accent. */
  accent: string;
};

// ── The 6 scored dimensions ───────────────────────────────────────────────────
// Each gets its own icon + a stable hue so it reads as a consistent "lane".
export const DIMENSION_ICONS: Record<string, Concept> = {
  api_surface: { icon: Plug, accent: "var(--t-green)" },
  auth: { icon: Lock, accent: "var(--t-amber)" },
  error_quality: { icon: TriangleAlert, accent: "var(--t-red)" },
  idempotency: { icon: Repeat, accent: "#a78bfa" },
  mcp_availability: { icon: Boxes, accent: "var(--t-blue)" },
  docs: { icon: BookOpen, accent: "#34d399" },
  general: { icon: Network, accent: "var(--primary)" },
};

export const DIMENSION_META: Record<
  string,
  { label: string; weight: number; concept: Concept }
> = {
  api_surface: {
    label: "API surface",
    weight: 20,
    concept: DIMENSION_ICONS.api_surface,
  },
  auth: { label: "Agent auth", weight: 20, concept: DIMENSION_ICONS.auth },
  error_quality: {
    label: "Error quality",
    weight: 15,
    concept: DIMENSION_ICONS.error_quality,
  },
  idempotency: {
    label: "Idempotency",
    weight: 15,
    concept: DIMENSION_ICONS.idempotency,
  },
  mcp_availability: {
    label: "MCP availability",
    weight: 20,
    concept: DIMENSION_ICONS.mcp_availability,
  },
  docs: { label: "Agent docs", weight: 10, concept: DIMENSION_ICONS.docs },
};

export function dimensionConcept(dim: string): Concept {
  return DIMENSION_ICONS[dim] ?? { icon: Network, accent: "var(--t-blue)" };
}

// ── Classify (API path vs Site path) ──────────────────────────────────────────
export function classifyConcept(kind: "api" | "site"): Concept {
  return kind === "api"
    ? { icon: Plug, accent: "var(--t-green)" }
    : { icon: Globe, accent: "var(--t-blue)" };
}

// ── Tool calls — pick an icon by tool-name shape ──────────────────────────────
export function toolConcept(name: string): Concept {
  const n = name.toLowerCase();
  if (/(browser|navigate|click|screenshot|page|dom|render)/.test(n))
    return { icon: Globe, accent: "var(--t-blue)" };
  if (/(mcp|proxy|tool)/.test(n)) return { icon: Plug, accent: "#a78bfa" };
  if (/(search|find|query|lookup)/.test(n))
    return { icon: Search, accent: "#34d399" };
  if (/(code|exec|run|shell|bash|script)/.test(n))
    return { icon: Terminal, accent: "var(--t-amber)" };
  if (/(http|fetch|request|get|post|put|delete|call|api)/.test(n))
    return { icon: ArrowLeftRight, accent: "var(--t-green)" };
  // Default — a generic HTTP exchange.
  return { icon: ArrowLeftRight, accent: "var(--t-muted)" };
}

// ── Probe — well-known path checks ────────────────────────────────────────────
export const PROBE_CONCEPT: Concept = { icon: FileJson, accent: "var(--t-blue)" };

// ── Sandbox / agent spawn ─────────────────────────────────────────────────────
export const SANDBOX_CONCEPT: Concept = { icon: Cpu, accent: "var(--t-amber)" };

// ── MCP / proxy ───────────────────────────────────────────────────────────────
export const MCP_CONCEPT: Concept = { icon: Boxes, accent: "var(--primary)" };
export const SERVER_CONCEPT: Concept = { icon: Server, accent: "var(--primary)" };
