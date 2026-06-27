"use client";

import { useEffect, useState, useCallback } from "react";
import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

// ── Name corpus (mirrors backend, works offline too) ──────────────────────────
const ADJ  = ["cosmic","silent","neon","blazing","velvet","phantom","midnight","steel","frozen","crimson","electric","hollow","obsidian","spectral","gilded","quantum","verdant","ashen","amber","scarlet","onyx","azure","silver","brutal","ancient","feral","solemn","radiant","cryptic","molten"];
const NOUN = ["badger","falcon","nebula","glacier","corvus","panther","vortex","titan","specter","lynx","raven","cipher","wraith","condor","vector","prism","harrier","mantis","jackal","herald","drifter","signal","axiom","current","eclipse","haven","vertex","relay","nomad","flare"];

function randomName() {
  return `${ADJ[Math.floor(Math.random() * ADJ.length)]}-${NOUN[Math.floor(Math.random() * NOUN.length)]}`;
}

function toDisplay(slug: string) {
  return slug.split("-").map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
}

// ── Google wordmark ───────────────────────────────────────────────────────────
function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  );
}

export default function SignInPage() {
  const params        = useSearchParams();
  const callbackUrl   = params.get("callbackUrl") ?? "/dashboard";
  const [guestName, setGuestName]   = useState<string>("");
  const [guestLoading, setGuestLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [flipping, setFlipping] = useState(false);

  // Pre-fetch a name from the backend; fall back to local generation
  useEffect(() => {
    let cancelled = false;
    async function fetchName() {
      try {
        const r = await fetch(`${BACKEND_URL}/api/v1/auth/guest-name`);
        if (!cancelled && r.ok) {
          const { name } = await r.json() as { name: string };
          setGuestName(name);
          return;
        }
      } catch {}
      if (!cancelled) setGuestName(randomName());
    }
    fetchName();
    return () => { cancelled = true; };
  }, []);

  const reroll = useCallback(() => {
    setFlipping(true);
    setTimeout(() => {
      setGuestName(randomName());
      setFlipping(false);
    }, 120);
  }, []);

  async function handleGoogle() {
    setGoogleLoading(true);
    await signIn("google", { callbackUrl });
  }

  async function handleGuest() {
    if (!guestName) return;
    setGuestLoading(true);
    try {
      const r = await fetch(`${BACKEND_URL}/api/v1/auth/guest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: guestName }),
      });
      if (!r.ok) throw new Error("Guest auth failed");
      const { access_token } = await r.json() as { access_token: string };
      await signIn("guest", { token: access_token, name: guestName, callbackUrl });
    } catch {
      setGuestLoading(false);
    }
  }

  const displayName = guestName ? toDisplay(guestName) : "…";

  return (
    <div
      className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden"
      style={{ background: "var(--background)" }}
    >
      {/* Subtle bloom */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 40% at 50% 20%, oklch(0.65 0.16 240 / 0.08) 0%, transparent 70%)",
        }}
      />

      <div className="relative z-10 flex w-full max-w-sm flex-col gap-8 px-6">
        {/* Wordmark */}
        <div className="text-center">
          <p className="eyebrow mb-3" style={{ color: "var(--muted-foreground)" }}>
            agent-readiness platform
          </p>
          <h1
            className="font-display text-[1.75rem] font-semibold tracking-tight"
            style={{ color: "var(--foreground)" }}
          >
            AgentReady
          </h1>
        </div>

        {/* Buttons */}
        <div className="flex flex-col gap-3">
          {/* Google */}
          <button
            onClick={handleGoogle}
            disabled={googleLoading || guestLoading}
            className={cn(
              "flex h-11 w-full items-center justify-center gap-2.5 rounded",
              "border text-sm font-medium transition-colors duration-[80ms] ease-linear",
              "hover:bg-surface-2 disabled:opacity-50"
            )}
            style={{
              borderColor: "var(--border-strong)",
              background: "var(--surface-1)",
              color: "var(--foreground)",
            }}
          >
            {googleLoading
              ? <span className="h-4 w-4 rounded-full border-2 border-current border-t-transparent animate-spin" />
              : <GoogleIcon />
            }
            Continue with Google
          </button>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="h-px flex-1" style={{ background: "var(--border)" }} />
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>or</span>
            <div className="h-px flex-1" style={{ background: "var(--border)" }} />
          </div>

          {/* Guest */}
          <div className="flex gap-1.5">
            <button
              onClick={handleGuest}
              disabled={guestLoading || googleLoading || !guestName}
              className={cn(
                "relative flex h-11 flex-1 items-center justify-center gap-2 rounded",
                "text-sm font-medium transition-colors duration-[80ms] ease-linear",
                "bg-[oklch(0.65_0.16_240)] hover:bg-[oklch(0.69_0.16_240)] disabled:opacity-50"
              )}
              style={{ color: "#fff" }}
            >
              {guestLoading
                ? <span className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
                : (
                  <span className={cn("transition-opacity duration-120", flipping && "opacity-0")}>
                    Enter as{" "}
                    <span className="font-semibold">{displayName}</span>
                  </span>
                )
              }
              {!guestLoading && <span className="opacity-70">→</span>}
            </button>

            {/* Re-roll */}
            <button
              onClick={reroll}
              disabled={guestLoading || googleLoading}
              title="Get a different name"
              className={cn(
                "flex h-11 w-11 items-center justify-center rounded border",
                "text-sm transition-colors duration-[80ms] ease-linear hover:bg-surface-2 disabled:opacity-40"
              )}
              style={{ borderColor: "var(--border)", color: "var(--muted-foreground)" }}
            >
              ↻
            </button>
          </div>

          {/* Guest note */}
          <p className="text-center text-[11px]" style={{ color: "var(--fg-subtle)" }}>
            Guest sessions last 7 days · no email required
          </p>
        </div>

        {/* Footer */}
        <p className="text-center text-[11px]" style={{ color: "var(--fg-subtle)" }}>
          By continuing you agree to our{" "}
          <a href="/terms" className="underline underline-offset-2 hover:text-foreground">terms</a>
        </p>
      </div>
    </div>
  );
}
