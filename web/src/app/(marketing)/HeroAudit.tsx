"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { CtaButton } from "@/components/CtaButton";
import { AccessFields, buildAccess, emptyAccess, type AccessState } from "@/components/AccessFields";
import { RunUpsell, readRunLimit, type AccessStatus } from "@/components/AccessGate";
import { track } from "@/components/global/Analytics";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export function HeroAudit() {
  const router = useRouter();
  const { data: session, status: sessionStatus } = useSession();
  const [domain, setDomain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [access, setAccess] = useState<AccessState>(emptyAccess);
  // When set, the free-run limit was hit → show the inline upsell instead of an error.
  const [limit, setLimit] = useState<AccessStatus | null | true>(null);

  // Returning from sign-in we carry the intended domain in ?domain= — prefill it.
  // Read from the URL directly (no useSearchParams → no Suspense requirement on
  // the statically-rendered marketing page).
  useEffect(() => {
    const d = new URLSearchParams(window.location.search).get("domain");
    if (d) setDomain(d);
  }, []);

  async function runAudit(e: React.FormEvent) {
    e.preventDefault();
    const raw = domain.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!raw) return;

    // Gate: a run requires a signed-in account. Send the visitor to sign-in and
    // bring them right back to the landing hero with their domain prefilled.
    if (sessionStatus !== "loading" && !session?.backendToken) {
      const next = `/?domain=${encodeURIComponent(raw)}`;
      router.push(`/signin?callbackUrl=${encodeURIComponent(next)}`);
      return;
    }

    setError(null);
    setLimit(null);
    setSubmitting(true);

    try {
      // Kick off the run, then hand off to the run cockpit — all the live
      // streaming + verdict happens on /run/{id}, never on the landing page.
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
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `HTTP ${res.status}`);
      }
      const { run_id } = (await res.json()) as { run_id: string };
      track("run_started", { domain: raw });
      router.push(`/run/${run_id}?domain=${encodeURIComponent(raw)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setSubmitting(false);
    }
  }

  return (
    <div className="w-full max-w-2xl space-y-4">
      {/* Domain input — one clean borderless bar */}
      <form
        onSubmit={runAudit}
        className="group flex h-12 items-center gap-2 rounded-lg pl-4 pr-1.5"
        style={{ background: "var(--surface-1)" }}
      >
        <span
          className="font-mono text-sm select-none"
          style={{ color: "var(--fg-subtle)" }}
        >
          https://
        </span>
        <input
          type="text"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="yourproduct.com"
          disabled={submitting}
          autoFocus
          className={cn(
            "h-10 flex-1 border-0 bg-transparent font-mono text-sm outline-none",
            "placeholder:text-fg-subtle disabled:opacity-60"
          )}
        />
        <CtaButton type="submit" disabled={submitting || !domain.trim()} size="sm">
          {submitting ? "Starting…" : "Test"}
        </CtaButton>
      </form>

      <AccessFields value={access} onChange={setAccess} disabled={submitting} />

      {/* Free-run limit reached → inline upsell (redeem code / checkout) */}
      {limit !== null && (
        <RunUpsell
          token={session?.backendToken}
          status={limit === true ? null : limit}
          onRedeemed={() => {
            setLimit(null);
            void runAudit({ preventDefault() {} } as React.FormEvent);
          }}
        />
      )}

      {/* Error */}
      {error && (
        <div
          className="rounded border px-3 py-2 text-xs"
          style={{
            borderColor: "oklch(0.53 0.22 20 / 0.3)",
            background: "oklch(0.53 0.22 20 / 0.06)",
            color: "oklch(0.53 0.22 20)",
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
