"use client";

import { useEffect, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";

export default function GitHubConnectPage() {
  const { data: session } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [msg, setMsg] = useState("");
  // Guard against double-exchange in React StrictMode (effect fires twice).
  const exchangedRef = useRef(false);

  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      setStatus("error");
      setMsg("No code in callback URL.");
      return;
    }
    if (!session?.backendToken) return;
    if (exchangedRef.current) return;
    exchangedRef.current = true;

    // The OAuth round-trip may carry a domain back through ?state= so we can
    // resume onboarding with the right product context.
    const domain = params.get("domain") ?? params.get("state") ?? "";
    const next = domain
      ? `/onboarding?domain=${encodeURIComponent(domain)}`
      : "/onboarding";

    fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1/github/connect`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.backendToken}`,
      },
      body: JSON.stringify({ code }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.connected) {
          setStatus("success");
          setTimeout(() => router.push(next), 1000);
        } else {
          setStatus("error");
          setMsg(JSON.stringify(d));
        }
      })
      .catch((e) => {
        setStatus("error");
        setMsg(String(e));
      });
  }, [session, params, router]);

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="text-center space-y-3">
        {status === "loading" && (
          <>
            <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-sm text-muted-foreground">Connecting GitHub…</p>
          </>
        )}
        {status === "success" && (
          <>
            <div className="text-2xl">✓</div>
            <p className="text-sm text-foreground">GitHub connected. Continuing setup…</p>
          </>
        )}
        {status === "error" && (
          <>
            <div className="text-2xl text-red-500">✗</div>
            <p className="text-sm text-red-500">Failed: {msg}</p>
          </>
        )}
      </div>
    </div>
  );
}
