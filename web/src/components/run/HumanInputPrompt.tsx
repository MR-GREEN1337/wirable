"use client";

// HumanInputPrompt — the agent paused mid-run and is waiting on a value from the
// human (an OTP, a credential, or free text). Rendered ANCHORED under the live
// agent viewport so the user immediately notices the agent is blocked on them.
//
// Lifecycle: RunView's reducer sets `pendingInput` on a `needs_input` event and
// clears it when the backend emits the resume `line` ("human input received…")
// or the run finishes. On submit we POST the value, show "sent, resuming…", and
// let the resume line dismiss the card. Visually unmissable (ciel-bleu accent +
// gentle pulse) but Lyra-tasteful: surface elevation, no gaudy shadows.

import { useEffect, useRef, useState } from "react";
import { KeyRound, Send } from "lucide-react";
import { BACKEND_URL } from "@/lib/run-events";
import { cn } from "@/lib/utils";

export type PendingInput = {
  prompt: string;
  kind?: string;
  request_id: string;
};

export function HumanInputPrompt({
  runId,
  pending,
}: {
  runId: string;
  pending: PendingInput;
}) {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isOtp = pending.kind === "otp";

  // Reset + autofocus whenever a new request comes in.
  useEffect(() => {
    setValue("");
    setSent(false);
    setError(null);
    const t = setTimeout(() => inputRef.current?.focus(), 60);
    return () => clearTimeout(t);
  }, [pending.request_id]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = value.trim();
    if (!v || submitting || sent) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/run/${runId}/input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: v }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Don't dismiss ourselves — the backend's resume `line` clears the card.
      setSent(true);
      setValue("");
    } catch {
      setError("Could not send. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="cn-enter relative overflow-hidden rounded-lg border p-4"
      style={{
        borderColor: "var(--primary)",
        background: "var(--primary-soft)",
      }}
      role="status"
      aria-live="polite"
    >
      {/* Gentle accent rail with a slow pulse so it reads as "waiting on you". */}
      <span
        className="pointer-events-none absolute inset-y-0 left-0 w-[3px]"
        style={{
          background: "var(--primary)",
          animation: "cn-input-pulse 1.6s ease-in-out infinite",
        }}
        aria-hidden
      />
      <style>{`
        @keyframes cn-input-pulse {
          0%, 100% { opacity: 0.45; }
          50% { opacity: 1; }
        }
      `}</style>

      <div className="flex items-start gap-3">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          <KeyRound className="h-4 w-4" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="eyebrow" style={{ color: "var(--primary)" }}>
            agent is waiting on you
          </div>
          <p
            className="mt-1 text-[14px] font-medium leading-snug"
            style={{ color: "var(--foreground)" }}
          >
            {pending.prompt}
          </p>

          <form onSubmit={submit} className="mt-3 flex items-center gap-2">
            <input
              ref={inputRef}
              type="text"
              inputMode={isOtp ? "numeric" : "text"}
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={submitting}
              placeholder={isOtp ? "123456" : "type your answer…"}
              className={cn(
                "h-10 flex-1 rounded border bg-transparent px-3 font-mono text-sm outline-none",
                "focus:border-primary focus:ring-1 focus:ring-primary/40 disabled:opacity-60",
                isOtp && "tracking-[0.3em]"
              )}
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
            />
            <button
              type="submit"
              disabled={submitting || !value.trim()}
              className={cn(
                "inline-flex h-10 items-center gap-1.5 rounded-md px-3.5 text-sm font-medium",
                "bg-primary text-primary-foreground transition-colors duration-[80ms]",
                "hover:bg-[oklch(0.69_0.16_240)]",
                "disabled:pointer-events-none disabled:opacity-50"
              )}
            >
              {submitting ? (
                <span
                  className="h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
                  style={{ animation: "spinner 0.8s linear infinite" }}
                />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
              Send
            </button>
          </form>

          {sent && !error && (
            <p className="mt-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              Sent — resuming the run…
            </p>
          )}
          {error && (
            <p className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
              {error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
