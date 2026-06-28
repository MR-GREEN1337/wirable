"use client";

// AuthModal — the one decision before generating the proxy: how the proxy should
// authenticate to the product on the agent's behalf. Most products are callable
// anonymously, so "Public" is preselected and the primary button works on the
// first click — the user can just confirm. The other options exist only for
// products that gate behind a key/token; in that case the proxy injects the
// credential server-side so agents never see it.
//
// Contract (preserved): export `AuthMode` + `AuthPayload`, and `onSubmit` takes
// an `AuthPayload`. RunView forwards it to POST /run/:id/proxy as `{ auth }`.

import { useEffect, useState } from "react";
import { X, Globe, KeyRound, Shield } from "lucide-react";
import { CtaButton } from "@/components/CtaButton";

export type AuthMode = "public" | "api_key" | "bearer";

export type AuthPayload =
  | { mode: "public" }
  | { mode: "api_key"; api_key: string }
  | { mode: "bearer"; token: string };

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
  icon: typeof Globe;
}[] = [
  {
    key: "public",
    label: "Public — no auth",
    blurb: "The agent calls the API anonymously. Pick this if the product is open.",
    icon: Globe,
  },
  {
    key: "api_key",
    label: "API key",
    blurb: "Paste a key; the proxy stores it server-side and injects it on each call.",
    icon: KeyRound,
  },
  {
    key: "bearer",
    label: "Bearer token",
    blurb: "Paste a bearer token; sent as `Authorization: Bearer …`, stored server-side.",
    icon: Shield,
  },
];

export function AuthModal({ open, submitting, onClose, onSubmit }: AuthModalProps) {
  // Public is preselected so the user can confirm and proceed immediately.
  const [mode, setMode] = useState<AuthMode>("public");
  const [secret, setSecret] = useState("");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const needsSecret = mode === "api_key" || mode === "bearer";
  const canSubmit = !needsSecret || secret.trim().length > 0;

  function submit() {
    if (mode === "api_key") {
      if (!secret.trim()) return;
      onSubmit({ mode: "api_key", api_key: secret.trim() });
    } else if (mode === "bearer") {
      if (!secret.trim()) return;
      onSubmit({ mode: "bearer", token: secret.trim() });
    } else {
      onSubmit({ mode: "public" });
    }
  }

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
          className="flex items-start justify-between border-b px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <div>
            <div className="eyebrow">proxy auth</div>
            <h2 className="font-display text-[16px] font-semibold">
              How does the proxy reach the product?
            </h2>
            <p className="mt-1 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              Most products are public — confirm and continue. If it needs a key,
              the proxy injects it server-side so agents never see it.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="cn-hover -mr-1 ml-2 rounded p-1"
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
                onClick={() => {
                  setMode(m.key);
                  setSecret("");
                }}
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

          {needsSecret && (
            <input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={mode === "api_key" ? "sk_live_…" : "eyJhbGc…"}
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
            {needsSecret
              ? "Stored encrypted, referenced by the proxy only."
              : "No credential leaves your browser."}
          </p>
          <CtaButton onClick={submit} size="sm" disabled={submitting || !canSubmit}>
            {submitting ? "Generating…" : "Generate proxy"}
          </CtaButton>
        </div>
      </div>
    </div>
  );
}
