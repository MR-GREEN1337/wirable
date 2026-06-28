"use client";

import { useEffect, useState } from "react";
import { Boxes } from "lucide-react";
import { Wordmark } from "@/components/global/Logo";
import { Favicon } from "@/components/run/Favicon";
import { scoreColor, scoreLabel } from "@/lib/run-events";
import { mcpSlug, mcpConfigJson, cursorDeepLink } from "@/lib/mcp-install";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

type Entry = { domain: string; score: number | null; mcp_url: string; tool_count: number };

function CopyConfig({ mcpUrl }: { mcpUrl: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(mcpConfigJson(mcpUrl, mcpSlug(mcpUrl))).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className="cn-hover rounded border px-2 py-1 text-[11px]"
      style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
    >
      {copied ? "copied" : "copy config"}
    </button>
  );
}

export default function RegistryPage() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    fetch(`${BACKEND_URL}/api/v1/registry`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d: Entry[]) => setEntries(Array.isArray(d) ? d : []))
      .catch(() => setErr(true));
  }, []);

  return (
    <div style={{ background: "var(--background)", color: "var(--foreground)", minHeight: "100vh" }}>
      <nav
        className="sticky top-0 z-40 border-b"
        style={{
          background: "color-mix(in oklch, var(--surface-1) 82%, transparent)",
          backdropFilter: "blur(12px)",
          borderColor: "var(--border)",
        }}
      >
        <div className="mx-auto flex h-12 max-w-[1000px] items-center px-6">
          <Wordmark href="/" size={22} />
        </div>
      </nav>

      <main className="mx-auto max-w-[1000px] px-6 py-12">
        <p className="eyebrow mb-2" style={{ color: "var(--muted-foreground)" }}>
          the directory
        </p>
        <h1 className="font-display text-[28px] font-semibold tracking-tight">
          Agent-ready products and their MCPs
        </h1>
        <p className="mt-2 max-w-xl text-[14px]" style={{ color: "var(--muted-foreground)" }}>
          Products tested by Wirable with a hosted MCP your agents can connect to. Add one to
          Cursor, or copy the config for any MCP client.
        </p>

        <div className="mt-8">
          {err ? (
            <p className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
              Could not load the directory.
            </p>
          ) : entries === null ? (
            <p className="text-[13px]" style={{ color: "var(--fg-subtle)" }}>
              Loading…
            </p>
          ) : entries.length === 0 ? (
            <div
              className="rounded-lg border px-6 py-12 text-center"
              style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
            >
              <p className="text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                No hosted MCPs yet. Run an audit and generate a proxy to be the first.
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border" style={{ borderColor: "var(--border)" }}>
              {entries.map((e, i) => {
                const color = e.score !== null ? scoreColor(e.score) : "var(--fg-subtle)";
                return (
                  <div
                    key={`${e.domain}-${i}`}
                    className="flex flex-wrap items-center gap-3 border-b px-4 py-3 last:border-b-0"
                    style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
                  >
                    <Favicon domain={e.domain} size={18} />
                    <span className="data min-w-0 flex-1 truncate text-[14px] font-medium">{e.domain}</span>
                    {e.score !== null && (
                      <span
                        className="data inline-flex items-center gap-2 rounded px-2 py-0.5 text-[13px] font-semibold"
                        style={{ color, border: `1px solid color-mix(in oklch, ${color} 35%, transparent)` }}
                      >
                        {e.score}
                        <span className="hidden text-[10px] uppercase tracking-[0.08em] sm:inline">
                          {scoreLabel(e.score)}
                        </span>
                      </span>
                    )}
                    <span className="inline-flex items-center gap-1 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                      <Boxes className="h-3.5 w-3.5" /> {e.tool_count} tools
                    </span>
                    <a
                      href={cursorDeepLink(e.mcp_url, mcpSlug(e.domain))}
                      className="cn-hover rounded px-2.5 py-1 text-[11px] font-semibold"
                      style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
                    >
                      Add to Cursor
                    </a>
                    <CopyConfig mcpUrl={e.mcp_url} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
