"use client";

// GitHub OAuth callback. GitHub redirects here with ?code=… after the user
// authorizes. We exchange the code via POST /api/v1/github/connect (authed with
// the session backend token), using the SAME redirect_uri string GitHub requires
// to match the authorize step, then bounce to /dashboard?github=connected.
//
// NOTE for ops: the GitHub OAuth app's Authorization callback URL must be
// `${origin}/github/callback` (e.g. https://wirable.app/github/callback).

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { AlertTriangle, Github } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";
import { BACKEND_URL } from "@/lib/run-events";

function Callback() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session, status: sessionStatus } = useSession();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  const code = params.get("code");
  const oauthError = params.get("error");

  useEffect(() => {
    if (ran.current) return;
    if (sessionStatus === "loading") return;

    if (oauthError) {
      setError(`GitHub authorization was cancelled (${oauthError}).`);
      ran.current = true;
      return;
    }
    if (!code) {
      setError("Missing authorization code from GitHub.");
      ran.current = true;
      return;
    }
    const token = session?.backendToken;
    if (!token) {
      setError("Your session expired. Sign in again, then reconnect GitHub.");
      ran.current = true;
      return;
    }

    ran.current = true;
    const redirectUri = `${window.location.origin}/github/callback`;
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/v1/github/connect`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ code, redirect_uri: redirectUri }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        router.replace("/dashboard?github=connected");
      } catch {
        setError("Could not complete the GitHub connection. Please try again.");
      }
    })();
  }, [code, oauthError, session, sessionStatus, router]);

  return (
    <div
      className="flex min-h-screen items-center justify-center px-6"
      style={{ background: "var(--background)", color: "var(--foreground)" }}
    >
      <div
        className="flex w-full max-w-sm flex-col items-center gap-4 rounded-lg border p-8 text-center"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <span
          className="flex h-10 w-10 items-center justify-center rounded-md border"
          style={{
            borderColor: "var(--border)",
            color: error ? "var(--danger)" : "var(--foreground)",
          }}
        >
          {error ? (
            <AlertTriangle className="h-5 w-5" strokeWidth={1.75} />
          ) : (
            <Github className="h-5 w-5" strokeWidth={1.75} />
          )}
        </span>

        {error ? (
          <>
            <p className="text-[13px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
              {error}
            </p>
            <CtaButton href="/dashboard" size="sm">
              Back to dashboard
            </CtaButton>
          </>
        ) : (
          <>
            <p className="eyebrow" style={{ color: "var(--muted-foreground)" }}>
              github
            </p>
            <div className="flex items-center gap-2 text-[14px]">
              <span
                className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
                style={{ animation: "spinner 0.8s linear infinite", color: "var(--primary)" }}
              />
              Connecting GitHub…
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function GithubCallbackPage() {
  // useSearchParams requires a Suspense boundary in the app router.
  return (
    <Suspense fallback={null}>
      <Callback />
    </Suspense>
  );
}
