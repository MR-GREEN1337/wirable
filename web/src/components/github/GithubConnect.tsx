"use client";

// Shared GitHub OAuth + repo-status logic, used by both the dashboard
// Integrations panel and the run page "Fix with GitHub" control.
//
// Flow:
//  1. Connect → GET /api/v1/github/authorize-url?redirect_uri=<origin>/github/callback
//     (unauthenticated) → window.location = url.
//  2. GitHub redirects to /github/callback?code=… which POSTs /github/connect.
//  3. status / repos / select are authed calls (Bearer session.backendToken).

import { useCallback, useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { BACKEND_URL } from "@/lib/run-events";

export type GithubStatus = { connected: boolean; repo: string | null };

export type GithubRepo = {
  full_name: string;
  name: string;
  private: boolean;
  default_branch: string;
  permissions?: Record<string, boolean> | null;
};

// Kick off the OAuth dance. The authorize-url endpoint is the one unauthenticated
// call. We send the SAME redirect_uri that the callback will replay (GitHub
// requires an exact match between authorize and token exchange).
export async function beginGithubConnect() {
  const redirectUri = `${window.location.origin}/github/callback`;
  const res = await fetch(
    `${BACKEND_URL}/api/v1/github/authorize-url?redirect_uri=${encodeURIComponent(
      redirectUri,
    )}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as { url: string };
  window.location.href = data.url;
}

// Hook: GitHub connection status + repo helpers, authed via the session token.
export function useGithub() {
  const { data: session, status: sessionStatus } = useSession();
  const token = session?.backendToken;

  const [status, setStatus] = useState<GithubStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!token) {
      setStatus(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/github/status`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus((await res.json()) as GithubStatus);
    } catch {
      setStatus({ connected: false, repo: null });
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (sessionStatus === "loading") return;
    void refresh();
  }, [sessionStatus, refresh]);

  const listRepos = useCallback(async (): Promise<GithubRepo[]> => {
    if (!token) return [];
    const res = await fetch(`${BACKEND_URL}/api/v1/github/repos`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as { repos: GithubRepo[]; selected?: string | null };
    return data.repos ?? [];
  }, [token]);

  const selectRepo = useCallback(
    async (repo: string) => {
      if (!token) return;
      const res = await fetch(`${BACKEND_URL}/api/v1/github/select`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ repo }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus({ connected: true, repo });
    },
    [token],
  );

  return {
    token,
    ready: sessionStatus !== "loading" && !loading,
    status,
    refresh,
    listRepos,
    selectRepo,
  };
}
