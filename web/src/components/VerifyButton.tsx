"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw, Loader2 } from "lucide-react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

/**
 * "Merge and verify" action. Calls POST /api/v1/fix/verify, which kicks off a
 * post-fix audit that streams over the audit SSE bus, then routes to the live
 * audit terminal at /audit/{job_id}.
 */
export function VerifyButton({ token }: { token: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleVerify() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/fix/verify`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = (await res.json()) as { job_id: string };
      router.push(`/audit/${job_id}`);
    } catch {
      setError("Couldn't start verification. Try again.");
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <button
        onClick={handleVerify}
        disabled={loading}
        className="group inline-flex items-center gap-2 rounded-xl bg-foreground px-4 py-2 text-sm font-medium text-background transition-transform hover:-translate-y-px disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <RefreshCw className="h-4 w-4" />
        )}
        Merge and verify
      </button>
      {error && (
        <span className="text-[11px]" style={{ color: "oklch(0.53 0.22 20)" }}>
          {error}
        </span>
      )}
    </div>
  );
}
