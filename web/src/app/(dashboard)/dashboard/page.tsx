import Link from "next/link";
import { Suspense } from "react";
import { ArrowRight, Boxes, ScanSearch } from "lucide-react";
import { auth } from "@/lib/auth";
import { CtaButton } from "@/components/CtaButton";
import { Favicon } from "@/components/run/Favicon";
import { DashboardRunInput } from "@/components/DashboardRunInput";
import { IntegrationsPanel } from "@/components/github/IntegrationsPanel";
import { scoreColor, scoreLabel } from "@/lib/run-events";

export const dynamic = "force-dynamic";

// Server-side: the backend base URL. Public var works server-side too; fall
// back to the server-only BACKEND_URL if that's what's configured.
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? process.env.BACKEND_URL ?? "";

type Target = {
  company_id: string;
  domain: string;
  name: string | null;
  score: number | null;
  confidence: number | null;
  last_run_at: string | null;
  proxy_status: "none" | "ready";
  mcp_url: string | null;
  agent_calls?: number | null;
};

type Dashboard = { state: string; targets: Target[] };

async function loadDashboard(token: string | undefined): Promise<Dashboard | null> {
  if (!token) return null;
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/dashboard`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as Dashboard;
  } catch {
    return null;
  }
}

function ScoreCell({ score }: { score: number | null }) {
  if (score === null || score === undefined) {
    return <span style={{ color: "var(--fg-subtle)" }}>—</span>;
  }
  const color = scoreColor(score);
  return (
    <span className="inline-flex items-center gap-2.5">
      <span
        className="data inline-flex items-center rounded px-1.5 py-0.5 font-display text-[14px] font-semibold"
        style={{
          color,
          background: `color-mix(in oklch, ${color} 10%, transparent)`,
          border: `1px solid color-mix(in oklch, ${color} 35%, transparent)`,
        }}
      >
        {score}
      </span>
      <span
        className="data hidden text-[10px] uppercase tracking-[0.08em] sm:inline"
        style={{ color }}
      >
        {scoreLabel(score)}
      </span>
    </span>
  );
}

export default async function DashboardPage() {
  const session = await auth();
  const data = await loadDashboard(session?.backendToken);
  const targets = data?.targets ?? [];

  const ready = targets.filter((t) => t.proxy_status === "ready").length;
  const scored = targets.filter((t) => t.score !== null && t.score !== undefined);
  const avg =
    scored.length > 0
      ? Math.round(scored.reduce((s, t) => s + (t.score ?? 0), 0) / scored.length)
      : null;

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
          new test
        </p>
        <h1 className="font-display text-2xl font-semibold tracking-tight">
          Test any product
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          Enter a URL. Wirable drives it as an agent and scores how usable it is.
        </p>
        <div className="mt-4 max-w-2xl">
          <DashboardRunInput />
        </div>
      </div>

      <div className="flex items-end justify-between pt-2">
        <div>
          <p className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
            targets
          </p>
          <h2 className="font-display text-lg font-semibold tracking-tight">
            Your runs
          </h2>
        </div>
      </div>

      {/* Stat strip */}
      {targets.length > 0 && (
        <div
          className="grid grid-cols-3 divide-x overflow-hidden rounded-md border"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          {[
            ["targets", String(targets.length), "var(--foreground)"],
            ["avg score", avg === null ? "—" : String(avg), avg === null ? "var(--fg-subtle)" : scoreColor(avg)],
            ["proxies live", String(ready), ready > 0 ? "var(--primary)" : "var(--fg-subtle)"],
          ].map(([label, value, color]) => (
            <div key={label} className="flex flex-col gap-1 px-5 py-4" style={{ borderColor: "var(--border)" }}>
              <span className="data font-display text-[23px] font-semibold leading-none" style={{ color }}>
                {value}
              </span>
              <span className="eyebrow text-[10px]">{label}</span>
            </div>
          ))}
        </div>
      )}

      {targets.length === 0 ? (
        <div
          className="relative overflow-hidden rounded-lg border px-6 py-16 text-center"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 h-40"
            style={{
              background:
                "radial-gradient(ellipse 60% 100% at 50% 0%, oklch(0.65 0.16 240 / 0.12) 0%, transparent 70%)",
            }}
          />
          <div className="relative mx-auto flex max-w-sm flex-col items-center gap-4">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-md border"
              style={{ borderColor: "var(--border)", color: "var(--primary)" }}
            >
              <ScanSearch className="h-5 w-5" strokeWidth={1.75} />
            </span>
            <p className="text-sm leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
              No runs yet. Test whether an agent can drive your platform, then host a
              proxy that fixes what breaks.
            </p>
            <CtaButton href="/" size="sm">
              Run your first test
            </CtaButton>
          </div>
        </div>
      ) : (
        <div
          className="overflow-hidden rounded border"
          style={{ borderColor: "var(--border)" }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr
                className="text-left"
                style={{ background: "var(--surface-2)", color: "var(--muted-foreground)" }}
              >
                <th className="px-4 py-2 font-medium">
                  <span className="eyebrow text-[10px]">Target</span>
                </th>
                <th className="px-4 py-2 font-medium">
                  <span className="eyebrow text-[10px]">Score</span>
                </th>
                <th className="px-4 py-2 font-medium">
                  <span className="eyebrow text-[10px]">Proxy</span>
                </th>
                <th className="px-4 py-2 text-right font-medium">
                  <span className="eyebrow text-[10px]">Agent calls</span>
                </th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => (
                <tr
                  key={t.company_id}
                  className="cn-hover border-t hover:bg-[var(--surface-2)]"
                  style={{ borderColor: "var(--border)" }}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <Favicon domain={t.domain} size={16} />
                      <div className="flex min-w-0 flex-col">
                        {t.name && (
                          <span className="truncate text-[13px] font-medium leading-tight">
                            {t.name}
                          </span>
                        )}
                        <span
                          className="data truncate text-[12px] leading-tight"
                          style={{ color: t.name ? "var(--muted-foreground)" : "var(--foreground)" }}
                        >
                          {t.domain}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <ScoreCell score={t.score} />
                  </td>
                  <td className="px-4 py-3">
                    {t.proxy_status === "ready" && t.mcp_url ? (
                      <a
                        href={t.mcp_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="cn-hover inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-[0.08em]"
                        style={{
                          color: "var(--primary)",
                          border: "1px solid color-mix(in oklch, var(--primary) 35%, transparent)",
                          background: "var(--primary-soft)",
                        }}
                        title={t.mcp_url}
                      >
                        <Boxes className="h-3 w-3" strokeWidth={2} />
                        MCP
                      </a>
                    ) : (
                      <span className="text-[12px]" style={{ color: "var(--fg-subtle)" }}>
                        none
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right data" style={{ color: "var(--muted-foreground)" }}>
                    {t.agent_calls ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/run/${t.company_id}?domain=${encodeURIComponent(t.domain)}`}
                      className="cn-hover group inline-flex items-center gap-1 text-[13px]"
                      style={{ color: "var(--primary)" }}
                    >
                      Re-test
                      <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Integrations — GitHub connect + repo picker (client island) */}
      <Suspense fallback={null}>
        <IntegrationsPanel />
      </Suspense>
    </div>
  );
}
