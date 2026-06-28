"use client";

// /access — the judges & hackathon-viewers entry point.
//
// Redeem an access code → unlimited runs. A shareable link of the form
//   https://<host>/access?code=JUDGE2026
// auto-redeems on load: if the visitor isn't signed in yet we kick off a guest
// session first (no email required), then redeem as soon as the token lands.
//
// Backend: POST /api/v1/access/redeem {code}  (authed)  → AccessStatus | 400.

import { useCallback, useEffect, useRef, useState } from "react";
import { signIn, useSession } from "next-auth/react";
import { CheckCircle2, KeyRound, Loader2 } from "lucide-react";
import { Logo } from "@/components/global/Logo";
import { CtaButton } from "@/components/CtaButton";
import { redeemCode } from "@/components/AccessGate";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

type Phase = "idle" | "signing-in" | "redeeming" | "granted" | "error";

export default function AccessPage() {
  const { data: session, status: sessionStatus } = useSession();
  const token = session?.backendToken;

  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);

  // The code pulled from ?code= — drives the auto-redeem flow.
  const autoCode = useRef<string | null>(null);
  // Guard so we only ever fire the auto flow once.
  const autoFired = useRef(false);

  // Start a guest session (no email) so a judge link "just works".
  const startGuest = useCallback(async () => {
    setPhase("signing-in");
    try {
      const nameRes = await fetch(`${BACKEND_URL}/api/v1/auth/guest-name`);
      const name = nameRes.ok
        ? ((await nameRes.json()) as { name: string }).name
        : `guest-${Date.now()}`;
      const r = await fetch(`${BACKEND_URL}/api/v1/auth/guest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error("Couldn’t start a guest session.");
      const { access_token } = (await r.json()) as { access_token: string };
      // No callbackUrl → next-auth updates the session in place; the effect below
      // then sees the token and redeems.
      await signIn("guest", { token: access_token, name, redirect: false });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
      setPhase("error");
    }
  }, []);

  const doRedeem = useCallback(
    async (raw: string, tok: string) => {
      setPhase("redeeming");
      setError(null);
      try {
        await redeemCode(tok, raw);
        setPhase("granted");
      } catch (err) {
        setError(err instanceof Error ? err.message : "That code didn’t work.");
        setPhase("error");
      }
    },
    [],
  );

  // Capture ?code= once on mount.
  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("code");
    if (c) {
      autoCode.current = c;
      setCode(c);
    }
  }, []);

  // Auto-redeem driver: wait for session to resolve, then sign in (guest) if
  // needed, then redeem the captured code.
  useEffect(() => {
    if (sessionStatus === "loading") return;
    if (!autoCode.current || autoFired.current) return;
    if (phase === "granted" || phase === "error") return;

    if (!token) {
      if (phase === "idle") void startGuest();
      return; // re-runs when the token lands
    }
    // We have a token + a pending code → redeem exactly once.
    autoFired.current = true;
    void doRedeem(autoCode.current, token);
  }, [sessionStatus, token, phase, startGuest, doRedeem]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    if (!token) {
      // Manual submit without a session → start guest, then this effect-less
      // path: stash the code and let the auto driver pick it up.
      autoCode.current = code.trim();
      autoFired.current = false;
      void startGuest();
      return;
    }
    autoFired.current = true;
    await doRedeem(code, token);
  }

  const busy = phase === "signing-in" || phase === "redeeming";

  return (
    <div
      className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden"
      style={{ background: "var(--background)" }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 40% at 50% 20%, oklch(0.65 0.16 240 / 0.08) 0%, transparent 70%)",
        }}
      />

      <div className="relative z-10 flex w-full max-w-sm flex-col gap-8 px-6">
        <div className="flex flex-col items-center text-center">
          <Logo size={44} className="mb-3" />
          <p className="eyebrow mb-2" style={{ color: "var(--muted-foreground)" }}>
            unlock unlimited runs
          </p>
          <h1
            className="font-display text-[1.75rem] font-semibold tracking-tight"
            style={{ color: "var(--foreground)" }}
          >
            Access code
          </h1>
        </div>

        {phase === "granted" ? (
          <div className="flex flex-col items-center gap-4 text-center">
            <span
              className="flex h-12 w-12 items-center justify-center rounded-full border"
              style={{
                borderColor: "color-mix(in oklch, var(--success) 40%, transparent)",
                color: "var(--success)",
              }}
            >
              <CheckCircle2 className="h-6 w-6" strokeWidth={1.75} />
            </span>
            <p className="text-[15px] font-medium" style={{ color: "var(--foreground)" }}>
              Access granted — unlimited runs
            </p>
            <p className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
              Your runs are now uncapped. Go test any product.
            </p>
            <CtaButton href="/dashboard" size="md">
              Open dashboard
            </CtaButton>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div
              className="flex h-11 items-center gap-2 rounded border px-3"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
            >
              <KeyRound
                className="h-4 w-4 shrink-0"
                strokeWidth={1.75}
                style={{ color: "var(--fg-subtle)" }}
              />
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="JUDGE2026"
                disabled={busy}
                autoFocus
                className="h-full flex-1 border-0 bg-transparent font-mono text-sm outline-none placeholder:text-fg-subtle disabled:opacity-60"
              />
            </div>

            <button
              type="submit"
              disabled={busy || !code.trim()}
              className={cn(
                "flex h-11 w-full items-center justify-center gap-2 rounded text-sm font-medium",
                "bg-[oklch(0.65_0.16_240)] hover:bg-[oklch(0.69_0.16_240)]",
                "transition-colors duration-[80ms] ease-linear disabled:opacity-50",
              )}
              style={{ color: "#fff" }}
            >
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {phase === "signing-in" ? "Signing you in…" : "Redeeming…"}
                </>
              ) : (
                "Redeem code"
              )}
            </button>

            {error && (
              <p className="text-center text-[12px]" style={{ color: "var(--danger)" }}>
                {error}
              </p>
            )}

            <p className="text-center text-[11px]" style={{ color: "var(--fg-subtle)" }}>
              Free for judges &amp; hackathon viewers · no email required
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
