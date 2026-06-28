"use client";

import { useState } from "react";
import { Copy, Check, KeyRound, Boxes } from "lucide-react";
import { BACKEND_URL, type AdvertiseBundle, type ProxyTool } from "@/lib/run-events";
import { McpMonitor } from "./McpMonitor";
import { mcpSlug, mcpConfigJson, cursorDeepLink } from "@/lib/mcp-install";

/** A distinctive small MCP mark — three connected nodes (a tool graph). */
function McpBadge() {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
      style={{
        color: "var(--primary)",
        border: "1px solid var(--primary)",
        background: "var(--primary-soft)",
      }}
    >
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
        <circle cx="6" cy="2.2" r="1.6" fill="currentColor" />
        <circle cx="2.4" cy="9" r="1.6" fill="currentColor" />
        <circle cx="9.6" cy="9" r="1.6" fill="currentColor" />
        <path d="M6 2.2 2.4 9M6 2.2 9.6 9M2.4 9h7.2" stroke="currentColor" strokeWidth="0.9" strokeLinecap="round" />
      </svg>
      MCP
    </span>
  );
}

interface ProxyPanelProps {
  proxyId: string;
  mcpUrl: string;
  tools: ProxyTool[];
  advertise: AdvertiseBundle;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className="cn-hover inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px]"
      style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" style={{ color: "var(--success)" }} /> copied
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" /> copy
        </>
      )}
    </button>
  );
}

type Tab = "well_known" | "llms_txt" | "link_tag" | "header";

const TABS: { key: Tab; label: string }[] = [
  { key: "well_known", label: ".well-known/mcp.json" },
  { key: "llms_txt", label: "llms.txt" },
  { key: "link_tag", label: "<link> tag" },
  { key: "header", label: "Link header" },
];

export function ProxyPanel({ proxyId, mcpUrl, tools, advertise }: ProxyPanelProps) {
  const [tab, setTab] = useState<Tab>("well_known");
  const [keyValue, setKeyValue] = useState<string | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [keyErr, setKeyErr] = useState<string | null>(null);

  const snippet =
    tab === "well_known"
      ? JSON.stringify(advertise.well_known, null, 2)
      : tab === "llms_txt"
        ? advertise.llms_txt
        : tab === "link_tag"
          ? advertise.link_tag
          : advertise.header;

  async function createKey() {
    setIssuing(true);
    setKeyErr(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/proxy/${proxyId}/keys`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { key?: string; api_key?: string };
      setKeyValue(data.key ?? data.api_key ?? "(no key returned)");
    } catch {
      setKeyErr("Could not issue a key. Try again.");
    } finally {
      setIssuing(false);
    }
  }

  return (
    <div
      className="rounded border"
      style={{
        borderColor: "oklch(0.65 0.16 240 / 0.4)",
        background: "var(--surface-1)",
      }}
    >
      <div
        className="flex items-center gap-2.5 border-b px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <McpBadge />
        <span className="eyebrow" style={{ color: "var(--primary)" }}>
          proxy live
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: "var(--success)" }}
          />
          <span
            className="data text-[10px] uppercase tracking-[0.08em]"
            style={{ color: "var(--success)" }}
          >
            hosted
          </span>
        </span>
      </div>

      {/* Hosted MCP url */}
      <div className="px-4 py-3">
        <div className="eyebrow mb-1.5 text-[10px]">hosted mcp endpoint</div>
        <div
          className="flex items-center gap-2 rounded border px-2.5 py-2"
          style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
        >
          <code
            className="min-w-0 flex-1 truncate font-mono text-[12px]"
            style={{ color: "var(--foreground)" }}
          >
            {mcpUrl}
          </code>
          <CopyButton value={mcpUrl} />
        </div>
      </div>

      {/* Use this MCP — one-click adoption */}
      <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
        <div className="eyebrow mb-2 text-[10px]">use this mcp</div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={cursorDeepLink(mcpUrl, mcpSlug(mcpUrl))}
            className="cn-hover inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[12px] font-semibold"
            style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
          >
            Add to Cursor
          </a>
          <CopyButton value={mcpConfigJson(mcpUrl, mcpSlug(mcpUrl))} />
          <span className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
            copy the config for Claude Desktop / any MCP client
          </span>
        </div>
        <pre
          className="scrollbar-minimal mt-2 max-h-40 overflow-auto rounded px-3 py-2.5 font-mono text-[11px] leading-relaxed"
          style={{
            background: "var(--t-bg)",
            border: "1px solid var(--t-border)",
            color: "var(--t-fg)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {mcpConfigJson(mcpUrl, mcpSlug(mcpUrl))}
        </pre>
      </div>

      {/* Tools */}
      {tools.length > 0 && (
        <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
          <div className="eyebrow mb-2 text-[10px]">
            generated tools · {tools.length}
          </div>
          <ul className="flex flex-col gap-1">
            {tools.map((t) => (
              <li
                key={t.name}
                className="flex items-center gap-2.5 rounded px-2 py-1.5"
                style={{ background: "var(--surface-2)" }}
              >
                <Boxes
                  className="h-3.5 w-3.5 shrink-0"
                  style={{ color: "var(--primary)" }}
                  strokeWidth={1.75}
                />
                <code
                  className="shrink-0 font-mono text-[12px]"
                  style={{ color: "var(--primary)" }}
                >
                  {t.name}
                </code>
                <span
                  className="min-w-0 flex-1 truncate text-[12px]"
                  style={{ color: "var(--muted-foreground)" }}
                  title={t.description}
                >
                  {t.description}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Advertise tabs */}
      <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
        <div className="eyebrow mb-2 text-[10px]">
          advertise this so agents discover it
        </div>
        <div className="flex flex-wrap gap-1">
          {TABS.map((t) => {
            const active = t.key === tab;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className="rounded px-2 py-1 font-mono text-[11px] transition-colors duration-[80ms]"
                style={{
                  background: active ? "var(--primary-soft)" : "transparent",
                  color: active ? "var(--primary)" : "var(--muted-foreground)",
                  border: active
                    ? "1px solid var(--primary)"
                    : "1px solid var(--border)",
                }}
              >
                {t.label}
              </button>
            );
          })}
        </div>
        <div className="relative mt-2">
          <pre
            className="scrollbar-minimal max-h-56 overflow-auto rounded px-3 py-2.5 font-mono text-[11px] leading-relaxed"
            style={{
              background: "var(--t-bg)",
              border: "1px solid var(--t-border)",
              color: "var(--t-fg)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {snippet}
          </pre>
          <div className="absolute right-2 top-2">
            <CopyButton value={snippet} />
          </div>
        </div>
      </div>

      {/* Create agent key */}
      <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-[13px] font-medium">Agent key</div>
            <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              Issue a scoped key for an agent to call the proxy.
            </p>
          </div>
          <button
            type="button"
            onClick={createKey}
            disabled={issuing}
            className="cn-hover inline-flex shrink-0 items-center gap-1.5 rounded border px-3 py-1.5 text-[12px] font-medium disabled:opacity-50"
            style={{ borderColor: "var(--border-strong)", color: "var(--foreground)" }}
          >
            <KeyRound className="h-3.5 w-3.5" />
            {issuing ? "Issuing…" : "Create agent key"}
          </button>
        </div>
        {keyErr && (
          <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
            {keyErr}
          </p>
        )}
        {keyValue && (
          <div
            className="mt-2 flex items-center gap-2 rounded border px-2.5 py-2"
            style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
          >
            <code
              className="min-w-0 flex-1 truncate font-mono text-[12px]"
              style={{ color: "var(--foreground)" }}
            >
              {keyValue}
            </code>
            <CopyButton value={keyValue} />
          </div>
        )}
      </div>

      {/* MCP drift monitoring */}
      <McpMonitor />
    </div>
  );
}
