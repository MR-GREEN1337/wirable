"use client";

// AccessFields — the optional "give the agent access" disclosure shared by both
// run launchers (the landing HeroAudit + the DashboardRunInput). Collapsed by
// default so the launcher looks exactly like before; one subtle toggle line
// expands a compact credential form.
//
// The parent owns the state (so it can build the POST body) and renders this as
// a controlled component. Use `buildAccess(state)` to turn the local state into
// the `access` object the backend expects — it returns `undefined` when the run
// should stay anonymous (Public, or required fields empty), so the caller simply
// omits `access` from the body in that case.

import { useState } from "react";
import { ChevronRight, Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export type AccessMode = "none" | "password" | "api_key" | "bearer";

export type AccessState = {
  mode: AccessMode;
  email: string;
  password: string;
  apiKey: string;
  token: string;
  notes: string;
};

// The wire shape the backend's POST /api/v1/run accepts under `access`.
export type AccessObject = {
  mode: "none" | "password" | "api_key" | "bearer";
  email?: string;
  password?: string;
  api_key?: string;
  token?: string;
  notes?: string;
};

export const emptyAccess: AccessState = {
  mode: "none",
  email: "",
  password: "",
  apiKey: "",
  token: "",
  notes: "",
};

// Turn local form state into the `access` object — or `undefined` when the run
// should stay anonymous (so the caller omits the field entirely).
export function buildAccess(s: AccessState): AccessObject | undefined {
  const notes = s.notes.trim();
  if (s.mode === "none") return undefined;
  if (s.mode === "password") {
    if (!s.email.trim() || !s.password) return undefined;
    return {
      mode: "password",
      email: s.email.trim(),
      password: s.password,
      ...(notes ? { notes } : {}),
    };
  }
  if (s.mode === "api_key") {
    if (!s.apiKey.trim()) return undefined;
    return { mode: "api_key", api_key: s.apiKey.trim(), ...(notes ? { notes } : {}) };
  }
  // bearer
  if (!s.token.trim()) return undefined;
  return { mode: "bearer", token: s.token.trim(), ...(notes ? { notes } : {}) };
}

const MODES: { key: AccessMode; label: string }[] = [
  { key: "none", label: "Public" },
  { key: "password", label: "Email + password" },
  { key: "api_key", label: "API key" },
  { key: "bearer", label: "Bearer token" },
];

const fieldClass =
  "h-9 w-full rounded border bg-transparent px-3 font-mono text-[13px] outline-none focus:border-primary focus:ring-1 focus:ring-primary/40";

export function AccessFields({
  value,
  onChange,
  disabled,
}: {
  value: AccessState;
  onChange: (next: AccessState) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const set = (patch: Partial<AccessState>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="cn-hover inline-flex items-center gap-1.5 text-[12px]"
        style={{ color: "var(--muted-foreground)" }}
      >
        <ChevronRight
          className="h-3.5 w-3.5 transition-transform duration-[120ms]"
          style={{ transform: open ? "rotate(90deg)" : "none" }}
        />
        <Lock className="h-3 w-3" strokeWidth={1.75} />
        Give the agent access{" "}
        <span style={{ color: "var(--fg-subtle)" }}>(optional)</span>
      </button>

      {open && (
        <div
          className="cn-enter space-y-3 rounded-lg border p-3"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          {/* Mode selector */}
          <div className="flex flex-wrap gap-1.5">
            {MODES.map((m) => {
              const active = m.key === value.mode;
              return (
                <button
                  key={m.key}
                  type="button"
                  disabled={disabled}
                  onClick={() => set({ mode: m.key })}
                  className={cn(
                    "rounded border px-2.5 py-1 text-[12px] transition-colors duration-[80ms]",
                    "disabled:opacity-50"
                  )}
                  style={{
                    borderColor: active ? "var(--primary)" : "var(--border)",
                    background: active ? "var(--primary-soft)" : "transparent",
                    color: active ? "var(--primary)" : "var(--muted-foreground)",
                  }}
                >
                  {m.label}
                </button>
              );
            })}
          </div>

          {/* Mode-specific fields */}
          {value.mode === "password" && (
            <div className="space-y-2">
              <input
                type="email"
                autoComplete="off"
                value={value.email}
                onChange={(e) => set({ email: e.target.value })}
                placeholder="test-account@example.com"
                disabled={disabled}
                className={fieldClass}
                style={{ borderColor: "var(--border)" }}
              />
              <input
                type="password"
                autoComplete="off"
                value={value.password}
                onChange={(e) => set({ password: e.target.value })}
                placeholder="password"
                disabled={disabled}
                className={fieldClass}
                style={{ borderColor: "var(--border)" }}
              />
            </div>
          )}
          {value.mode === "api_key" && (
            <input
              type="password"
              autoComplete="off"
              value={value.apiKey}
              onChange={(e) => set({ apiKey: e.target.value })}
              placeholder="sk_live_…"
              disabled={disabled}
              className={fieldClass}
              style={{ borderColor: "var(--border)" }}
            />
          )}
          {value.mode === "bearer" && (
            <input
              type="password"
              autoComplete="off"
              value={value.token}
              onChange={(e) => set({ token: e.target.value })}
              placeholder="eyJhbGc…"
              disabled={disabled}
              className={fieldClass}
              style={{ borderColor: "var(--border)" }}
            />
          )}

          {value.mode !== "none" && (
            <input
              type="text"
              value={value.notes}
              onChange={(e) => set({ notes: e.target.value })}
              placeholder="notes for the agent (e.g. use the Demo workspace)"
              disabled={disabled}
              className={fieldClass}
              style={{ borderColor: "var(--border)" }}
            />
          )}

          <p className="text-[11px] leading-relaxed" style={{ color: "var(--fg-subtle)" }}>
            Used only to drive this run in an isolated sandbox. Prefer a
            throwaway/test account.
          </p>
        </div>
      )}
    </div>
  );
}
