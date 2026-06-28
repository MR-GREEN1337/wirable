"use client";

// AccessGate — the shared launch-gating surface for Wirable runs.
//
// Backend contract:
//  - GET  /api/v1/access/status  (authed) → AccessStatus
//  - POST /api/v1/access/redeem {code}    (authed) → AccessStatus (tier:"unlimited")
//  - POST /api/v1/billing/checkout (authed) → {url} | 503 if billing off
//  - POST /api/v1/run on 402 → { detail: { detail, upgrade, status } }  (NESTED)
//
// Exports:
//  - useAccess(): fetch + refresh entitlement status via session.backendToken
//  - <AccessChip />: remaining-free-runs pill for the dashboard nav
//  - <RunUpsell />: the "you've used your free runs" inline upsell (redeem +
//    get-access/checkout), reused by both run launchers.
//  - isRunLimitError(): typed reader for the nested 402 body.

import { useCallback, useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { Infinity as InfinityIcon, KeyRound, Loader2, Sparkles } from "lucide-react";
import { BACKEND_URL } from "@/lib/run-events";
import { track } from "@/components/global/Analytics";
import { cn } from "@/lib/utils";

export type AccessStatus = {
  tier: string;
  runs_used: number;
  runs_limit: number;
  remaining: number;
  unlimited: boolean;
};

// ── 402 reader ────────────────────────────────────────────────────────────────
// The run launchers throw a typed error carrying the parsed body so the upsell
// can render without a second fetch. The backend nests the real detail one level
// deep: { detail: { detail, upgrade, status } }.
export class RunLimitError extends Error {
  status?: AccessStatus;
  constructor(status?: AccessStatus) {
    super("run limit reached");
    this.name = "RunLimitError";
    this.status = status;
  }
}

// Inspect a POST /run response body to detect the run-limit (402) shape.
export function readRunLimit(body: unknown): AccessStatus | null | true {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const d = detail as { upgrade?: unknown; status?: unknown };
  if (d.upgrade !== true) return null;
  if (d.status && typeof d.status === "object") return d.status as AccessStatus;
  return true; // upgrade flagged but no status payload
}

// ── status hook ───────────────────────────────────────────────────────────────
export function useAccess() {
  const { data: session, status: sessionStatus } = useSession();
  const token = session?.backendToken;

  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!token) {
      setStatus(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/access/status`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus((await res.json()) as AccessStatus);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (sessionStatus === "loading") return;
    void refresh();
  }, [sessionStatus, refresh]);

  return {
    token,
    status,
    ready: sessionStatus !== "loading" && !loading,
    refresh,
    setStatus,
  };
}

// Redeem an access code against the current session. Returns the new status.
export async function redeemCode(
  token: string,
  code: string,
): Promise<AccessStatus> {
  const res = await fetch(`${BACKEND_URL}/api/v1/access/redeem`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ code: code.trim() }),
  });
  if (!res.ok) {
    let msg = "That code didn’t work.";
    try {
      const j = await res.json();
      if (typeof j?.detail === "string") msg = j.detail;
    } catch {}
    throw new Error(msg);
  }
  return (await res.json()) as AccessStatus;
}

// Begin Stripe checkout → redirect, or surface 503 (billing not configured).
export async function beginCheckout(token: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/v1/billing/checkout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 503) {
    throw new Error("Paid access isn’t available yet — use an access code.");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const { url } = (await res.json()) as { url: string };
  if (url) window.location.href = url;
}

// ── remaining-runs chip ─────────────────────────────────────────────────────
// Small entitlement pill for the dashboard nav. Renders nothing until status
// loads. Links to /access when out of free runs.
export function AccessChip({ className }: { className?: string }) {
  const { status, ready } = useAccess();
  if (!ready || !status) return null;

  if (status.unlimited) {
    return (
      <span
        className={cn(
          "inline-flex h-7 items-center gap-1.5 rounded border px-2 text-[11px] font-medium",
          className,
        )}
        style={{
          borderColor: "oklch(0.65 0.16 240 / 0.4)",
          background: "oklch(0.65 0.16 240 / 0.08)",
          color: "oklch(0.65 0.16 240)",
        }}
        title={status.tier === "paid" ? "Paid plan — unlimited runs" : "Unlimited runs"}
      >
        <InfinityIcon className="h-3 w-3" strokeWidth={2} />
        unlimited
      </span>
    );
  }

  const out = status.remaining <= 0;
  if (out) {
    return (
      <a
        href="/access"
        className={cn(
          "cn-hover inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[11px] font-medium",
          className,
        )}
        style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
      >
        <Sparkles className="h-3 w-3" strokeWidth={2} />
        Get access
      </a>
    );
  }

  return (
    <span
      className={cn(
        "data inline-flex h-7 items-center gap-1.5 rounded border px-2 text-[11px] font-medium",
        className,
      )}
      style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
      title={`${status.runs_used} of ${status.runs_limit} free runs used`}
    >
      {status.remaining} {status.remaining === 1 ? "run" : "runs"} left
    </span>
  );
}

// ── inline upsell ─────────────────────────────────────────────────────────────
// Shown by a launcher after a 402. Two paths: redeem an access code, or start
// checkout. `onRedeemed` lets the launcher refresh its state and retry.
export function RunUpsell({
  token,
  status,
  onRedeemed,
}: {
  token: string | undefined;
  status?: AccessStatus | null;
  onRedeemed?: (s: AccessStatus) => void;
}) {
  const [showCode, setShowCode] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<null | "redeem" | "checkout">(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRedeem(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !code.trim()) return;
    setError(null);
    setBusy("redeem");
    try {
      const next = await redeemCode(token, code);
      track("access_redeemed");
      onRedeemed?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code didn’t work.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCheckout() {
    if (!token) return;
    track("get_access_clicked");
    setError(null);
    setBusy("checkout");
    try {
      await beginCheckout(token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn’t start checkout.");
      setBusy(null);
    }
  }

  const used = status ? status.runs_limit : null;

  return (
    <div
      className="cn-enter space-y-3 rounded-lg border p-4"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="flex items-start gap-2.5">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border"
          style={{ borderColor: "var(--border)", color: "var(--primary)" }}
        >
          <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} />
        </span>
        <div className="space-y-0.5">
          <p className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>
            You’ve used your free runs
          </p>
          <p className="text-[12px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
            {used
              ? `That’s all ${used} free runs. Redeem an access code or upgrade to keep testing.`
              : "Redeem an access code or upgrade to keep testing."}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setShowCode((v) => !v)}
          className="cn-hover inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-[12px] font-medium"
          style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
        >
          <KeyRound className="h-3.5 w-3.5" strokeWidth={1.75} />
          Have an access code?
        </button>
        <button
          type="button"
          onClick={handleCheckout}
          disabled={busy !== null}
          className={cn(
            "inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-[12px] font-medium",
            "transition-colors duration-[80ms] ease-linear disabled:opacity-50",
          )}
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          {busy === "checkout" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} />
          )}
          Get access
        </button>
      </div>

      {showCode && (
        <form onSubmit={handleRedeem} className="flex gap-2">
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="access code"
            autoFocus
            className="h-9 flex-1 rounded border bg-transparent px-3 font-mono text-[13px] outline-none focus:border-primary focus:ring-1 focus:ring-primary/40"
            style={{ borderColor: "var(--border)" }}
          />
          <button
            type="submit"
            disabled={busy !== null || !code.trim()}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-[12px] font-medium transition-colors duration-[80ms] disabled:opacity-50"
            style={{ borderColor: "var(--border-strong)", color: "var(--foreground)" }}
          >
            {busy === "redeem" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Redeem"}
          </button>
        </form>
      )}

      {error && (
        <p className="text-[12px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </div>
  );
}
