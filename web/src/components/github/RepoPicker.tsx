"use client";

// Searchable repo picker. Lazily loads the repo list on first focus, filters
// client-side, and POSTs /github/select on choice. Lyra styling — surface-2
// dropdown, ciel-bleu active row, no shadows (popover exception kept minimal).

import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown, Lock, Search } from "lucide-react";
import type { GithubRepo } from "./GithubConnect";

export function RepoPicker({
  selected,
  listRepos,
  onSelect,
}: {
  selected: string | null;
  listRepos: () => Promise<GithubRepo[]>;
  // Accepts an async persist (global) OR a sync local setter (per-test).
  onSelect: (repo: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [repos, setRepos] = useState<GithubRepo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Load on first open.
  useEffect(() => {
    if (!open || repos !== null || loading) return;
    setLoading(true);
    listRepos()
      .then((r) => setRepos(r))
      .catch(() => setError("Could not load repositories."))
      .finally(() => setLoading(false));
  }, [open, repos, loading, listRepos]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = (repos ?? []).filter((r) =>
    r.full_name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  async function choose(repo: string) {
    setSaving(true);
    setError(null);
    try {
      await onSelect(repo);
      setOpen(false);
    } catch {
      setError("Could not select that repository.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={saving}
        className="cn-hover flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-[13px] disabled:opacity-60"
        style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
      >
        <span
          className="data min-w-0 truncate"
          style={{ color: selected ? "var(--foreground)" : "var(--fg-subtle)" }}
        >
          {saving ? "Saving…" : selected ?? "Select a repository"}
        </span>
        <ChevronsUpDown
          className="h-3.5 w-3.5 shrink-0"
          style={{ color: "var(--muted-foreground)" }}
        />
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-1)",
            boxShadow: "0 8px 24px oklch(0 0 0 / 0.18)",
          }}
        >
          <div
            className="flex items-center gap-2 border-b px-3 py-2"
            style={{ borderColor: "var(--border)" }}
          >
            <Search className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--fg-subtle)" }} />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search repositories…"
              className="data w-full bg-transparent text-[13px] outline-none"
              style={{ color: "var(--foreground)" }}
            />
          </div>

          <div className="max-h-64 overflow-y-auto py-1">
            {loading && (
              <p className="px-3 py-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Loading repositories…
              </p>
            )}
            {error && (
              <p className="px-3 py-2 text-[12px]" style={{ color: "var(--danger)" }}>
                {error}
              </p>
            )}
            {!loading && !error && filtered.length === 0 && (
              <p className="px-3 py-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                No repositories match.
              </p>
            )}
            {filtered.map((r) => {
              const active = r.full_name === selected;
              return (
                <button
                  key={r.full_name}
                  type="button"
                  onClick={() => choose(r.full_name)}
                  className="cn-hover flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-[var(--surface-2)]"
                  style={{ color: "var(--foreground)" }}
                >
                  <Check
                    className="h-3.5 w-3.5 shrink-0"
                    style={{ color: active ? "var(--primary)" : "transparent" }}
                  />
                  <span className="data min-w-0 flex-1 truncate">{r.full_name}</span>
                  {r.private && (
                    <Lock className="h-3 w-3 shrink-0" style={{ color: "var(--fg-subtle)" }} />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
