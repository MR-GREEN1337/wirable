"use client";

import { useEffect, useState } from "react";
import { X, KeyRound, ShieldCheck, MousePointerClick } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";

export type AuthMode = "api_key" | "oauth" | "session";

export type AuthPayload =
  | { mode: "api_key"; api_key: string }
  | { mode: "oauth" }
  | { mode: "session" };

interface AuthModalProps {
  open: boolean;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (auth: AuthPayload) => void;
}

const MODES: {
  key: AuthMode;
  label: string;
  blurb: string;
  icon: typeof KeyRound;
}[] = [
  {
    key: "api_key",
    label: "Paste API key",
    blurb: "The proxy holds the key and presents it on the agent's behalf.",
    icon: KeyRound,
  },
  {
    key: "oauth",
    label: "Connect OAuth",
    blurb: "Authorize once; the proxy stores a scoped, refreshable grant.",
    icon: ShieldCheck,
  },
  {
    key: "session",
    label: "Agents ride the user session",
    blurb: "Drive the live site under the signed-in browser session.",
    icon: MousePointerClick,
  },
];

export function AuthModal({ open, submitting, onClose, onSubmit }: AuthModalProps) {
  const [mode, setMode] = useState<AuthMode>("api_key");
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function submit() {
    if (mode === "api_key") {
      if (!apiKey.trim()) return;
      onSubmit({ mode: "api_key", api_key: apiKey.trim() });
    } else if (mode === "oauth") {
      onSubmit({ mode: "oauth" });
    } else {
      onSubmit({ mode: "session" });
    }
  }

  const canSubmit = mode !== "api_key" || apiKey.trim().length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "oklch(0.08 0.005 250 / 0.6)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border shadow-lg"
        style={{ background: "var(--surface-1)", borderColor: "var(--border-strong)" }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div
          className="flex items-center justify-between border-b px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <div>
            <div className="eyebrow">configure auth</div>
            <h2 className="font-display text-[16px] font-semibold">
              How should agents authenticate?
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="cn-hover -mr-1 rounded p-1"
            style={{ color: "var(--muted-foreground)" }}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-col gap-2 px-5 py-4">
          {MODES.map((m) => {
            const active = m.key === mode;
            const Icon = m.icon;
            return (
              <button
                key={m.key}
                type="button"
                onClick={() => setMode(m.key)}
                className="flex items-start gap-3 rounded border px-3 py-2.5 text-left transition-colors duration-[80ms]"
                style={{
                  borderColor: active ? "var(--primary)" : "var(--border)",
                  background: active ? "var(--primary-soft)" : "var(--surface-1)",
                }}
              >
                <Icon
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: active ? "var(--primary)" : "var(--muted-foreground)" }}
                  strokeWidth={1.75}
                />
                <div className="min-w-0">
                  <div
                    className="text-[13px] font-medium"
                    style={{ color: active ? "var(--primary)" : "var(--foreground)" }}
                  >
                    {m.label}
                  </div>
                  <p
                    className="mt-0.5 text-[12px] leading-relaxed"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {m.blurb}
                  </p>
                </div>
              </button>
            );
          })}

          {mode === "api_key" && (
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk_live_…"
              autoFocus
              className="mt-1 h-10 w-full rounded border bg-surface-1 px-3 font-mono text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary/40"
              style={{ borderColor: "var(--border)" }}
            />
          )}
        </div>

        <div
          className="flex items-center justify-between gap-3 border-t px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <p className="text-[11px]" style={{ color: "var(--fg-subtle)" }}>
            Stored encrypted, referenced by the proxy only.
          </p>
          <CtaButton onClick={submit} size="sm" disabled={submitting || !canSubmit}>
            {submitting ? "Generating…" : "Generate proxy"}
          </CtaButton>
        </div>
      </div>
    </div>
  );
}
