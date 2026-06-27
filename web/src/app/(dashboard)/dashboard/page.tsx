import Link from "next/link";
import { auth } from "@/lib/auth";
import { CtaButton } from "@/components/CtaButton";
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
    <span className="inline-flex items-center gap-2">
      <span className="data font-display text-[15px] font-semibold" style={{ color }}>
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
            targets
          </p>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Your runs
          </h1>
        </div>
        <CtaButton href="/">Run a new test</CtaButton>
      </div>

      {targets.length === 0 ? (
        <div
          className="rounded border px-6 py-10 text-center text-sm"
          style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
        >
          No runs yet. Test whether an agent can drive your platform — then host a
          proxy that fixes what breaks.
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
                  className="border-t"
                  style={{ borderColor: "var(--border)" }}
                >
                  <td className="px-4 py-3 font-mono">{t.domain}</td>
                  <td className="px-4 py-3">
                    <ScoreCell score={t.score} />
                  </td>
                  <td className="px-4 py-3">
                    {t.proxy_status === "ready" && t.mcp_url ? (
                      <a
                        href={t.mcp_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="cn-hover inline-flex items-center gap-1.5 font-mono text-[12px]"
                        style={{ color: "var(--primary)" }}
                        title={t.mcp_url}
                      >
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: "var(--primary)" }}
                        />
                        mcp_url
                      </a>
                    ) : (
                      <span style={{ color: "var(--fg-subtle)" }}>none</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right data" style={{ color: "var(--muted-foreground)" }}>
                    {t.agent_calls ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/run/${t.company_id}?domain=${encodeURIComponent(t.domain)}`}
                      className="cn-hover text-[13px]"
                      style={{ color: "var(--primary)" }}
                    >
                      Re-test →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
