"use client";

// Dashboard "Integrations" card. Shows GitHub CONNECTION status only:
//  - not connected → "Connect GitHub" (kicks the OAuth flow)
//  - connected     → "connected" pill
// The repo is NOT chosen here — it's scoped per test (picked on each run's page),
// because different runs target different products.
// Reads ?github=connected from the callback redirect to confirm + refresh.

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Github, CheckCircle2 } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";
import { beginGithubConnect, useGithub } from "./GithubConnect";

export function IntegrationsPanel() {
  const { ready, status, refresh } = useGithub();
  const params = useSearchParams();
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Just came back from the OAuth callback — refresh status.
  useEffect(() => {
    if (params.get("github") === "connected") {
      void refresh();
    }
  }, [params, refresh]);

  async function onConnect() {
    setConnecting(true);
    setError(null);
    try {
      await beginGithubConnect();
    } catch {
      setError("Could not start GitHub connect. Try again.");
      setConnecting(false);
    }
  }

  const connected = status?.connected;

  return (
    <section
      className="rounded-lg border p-5"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border"
            style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
          >
            <Github className="h-4.5 w-4.5" strokeWidth={1.75} style={{ width: 18, height: 18 }} />
          </span>
          <div>
            <p className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
              integrations
            </p>
            <h2 className="font-display text-[16px] font-semibold leading-tight">GitHub</h2>
            <p className="mt-1 text-[13px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
              Connect once so any failing run can open a fix PR. You pick which repo
              to fix per test, on the run&apos;s page. Runs can target different products.
            </p>
          </div>
        </div>

        {ready && connected && (
          <span
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px] uppercase tracking-[0.08em]"
            style={{
              border: "1px solid color-mix(in oklch, var(--success) 40%, transparent)",
              color: "var(--success)",
            }}
          >
            <CheckCircle2 className="h-3 w-3" strokeWidth={2} />
            connected
          </span>
        )}
      </div>

      <div className="mt-4">
        {!ready ? (
          <p className="text-[12px]" style={{ color: "var(--fg-subtle)" }}>
            Loading…
          </p>
        ) : connected ? (
          <p className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
            GitHub is connected. Open any run and choose the repo to fix from there.
          </p>
        ) : (
          <CtaButton onClick={onConnect} size="sm" disabled={connecting}>
            {connecting ? "Redirecting…" : "Connect GitHub"}
          </CtaButton>
        )}
        {error && (
          <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
            {error}
          </p>
        )}
      </div>
    </section>
  );
}
