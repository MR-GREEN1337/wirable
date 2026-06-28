"use client";

// Dashboard run launcher — same one-line flow as the landing hero: type a URL,
// POST /run, hand off to the /run/{id} cockpit. Lets you start a test for ANY
// product right from the dashboard (each run is independent — repos are chosen
// per test on the run page, not globally).

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { ScanSearch } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";
import { AccessFields, buildAccess, emptyAccess, type AccessState } from "@/components/AccessFields";
import { RunUpsell, readRunLimit, type AccessStatus } from "@/components/AccessGate";
import { track } from "@/components/global/Analytics";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export function DashboardRunInput() {
  const router = useRouter();
  const { data: session, status: sessionStatus } = useSession();
  const [domain, setDomain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [access, setAccess] = useState<AccessState>(emptyAccess);
  // When set, the free-run limit was hit → show the inline upsell.
  const [limit, setLimit] = useState<AccessStatus | null | true>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const raw = domain.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!raw) return;

    // Gate: runs require a signed-in account. (The dashboard layout already
    // redirects unauthenticated users, but guard here too in case the token
    // lapsed.)
    if (sessionStatus !== "loading" && !session?.backendToken) {
      router.push("/signin");
      return;
    }

    setError(null);
    setLimit(null);
    setSubmitting(true);
    try {
      const accessObj = buildAccess(access);
      const res = await fetch(`${BACKEND_URL}/api/v1/run`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session!.backendToken}`,
        },
        body: JSON.stringify({ url: raw, ...(accessObj ? { access: accessObj } : {}) }),
      });
      if (res.status === 401) {
        router.push("/signin");
        return;
      }
      if (res.status === 402) {
        const body = await res.json().catch(() => null);
        setLimit(readRunLimit(body) ?? true);
        setSubmitting(false);
        return;
      }
      if (!res.ok) throw new Error(await res.text());
      const { run_id } = (await res.json()) as { run_id: string };
      track("run_started", { domain: raw });
      router.push(`/run/${run_id}?domain=${encodeURIComponent(raw)}`);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Could not start the run.");
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-2">
      <form
        onSubmit={run}
        className="flex h-12 items-center gap-2 rounded-lg border pl-3 pr-1.5"
        style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
      >
        <ScanSearch className="h-4 w-4 shrink-0" strokeWidth={1.75} style={{ color: "var(--fg-subtle)" }} />
        <span className="font-mono text-sm select-none" style={{ color: "var(--fg-subtle)" }}>
          https://
        </span>
        <input
          type="text"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="test any product: stripe.com, yourapp.com…"
          disabled={submitting}
          className={cn(
            "h-10 flex-1 border-0 bg-transparent font-mono text-sm outline-none",
            "placeholder:text-fg-subtle disabled:opacity-60"
          )}
        />
        <CtaButton type="submit" disabled={submitting || !domain.trim()} size="sm">
          {submitting ? "Starting…" : "Run test"}
        </CtaButton>
      </form>
      <AccessFields value={access} onChange={setAccess} disabled={submitting} />
      {limit !== null && (
        <RunUpsell
          token={session?.backendToken}
          status={limit === true ? null : limit}
          onRedeemed={() => {
            setLimit(null);
            void run({ preventDefault() {} } as React.FormEvent);
          }}
        />
      )}
      {error && (
        <p className="text-[12px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
