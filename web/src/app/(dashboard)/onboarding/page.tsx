"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import { useSession } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Check,
  Github,
  Search,
  Lock,
  Globe,
  ArrowRight,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

/* ── Types ─────────────────────────────────────────────────────────────────── */

type Repo = { full_name: string; private: boolean };

type Step = 1 | 2 | 3;

/* ── Progress indicator ───────────────────────────────────────────────────────── */

function Stepper({ step, claimed, connected }: { step: Step; claimed: boolean; connected: boolean }) {
  const items = [
    { n: 1 as Step, label: "Claim product", done: claimed },
    { n: 2 as Step, label: "Connect GitHub", done: connected },
    { n: 3 as Step, label: "Pick a repo", done: false },
  ];

  return (
    <div className="flex items-center gap-2">
      {items.map((it, i) => {
        const active = step === it.n;
        const complete = it.done && !active;
        return (
          <div key={it.n} className="flex items-center gap-2">
            <div className="flex items-center gap-2">
              <div
                className="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold data transition-colors"
                style={{
                  background: complete
                    ? "oklch(0.52 0.17 152)"
                    : active
                      ? "var(--primary)"
                      : "var(--surface-3)",
                  color:
                    complete || active
                      ? "var(--primary-foreground)"
                      : "var(--muted-foreground)",
                }}
              >
                {complete ? <Check className="h-3.5 w-3.5" /> : it.n}
              </div>
              <span
                className="text-xs font-medium"
                style={{
                  color: active
                    ? "var(--foreground)"
                    : "var(--muted-foreground)",
                }}
              >
                {it.label}
              </span>
            </div>
            {i < items.length - 1 && (
              <div
                className="h-px w-6"
                style={{ background: "var(--border)" }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Card shell ───────────────────────────────────────────────────────────────── */

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded border p-6"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      {children}
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────────── */

export default function OnboardingPage() {
  const { data: session, status: sessionStatus } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const token = session?.backendToken;

  const prefillDomain = params.get("domain") ?? "";

  const [step, setStep] = useState<Step>(1);

  // Step 1 — claim
  const [domain, setDomain] = useState(prefillDomain);
  const [founderName, setFounderName] = useState("");
  const [founderEmail, setFounderEmail] = useState("");
  const [claiming, setClaiming] = useState(false);
  const [claimed, setClaimed] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);

  // Step 2 — github
  const [githubConnected, setGithubConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  // Step 3 — repos
  const [repos, setRepos] = useState<Repo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [savingRepo, setSavingRepo] = useState(false);
  const [startingFix, setStartingFix] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);

  // Keep domain in sync if query param arrives after first render
  useEffect(() => {
    if (prefillDomain && !domain) setDomain(prefillDomain);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillDomain]);

  // On mount (once authed), hydrate from the dashboard so refresh/return-trips
  // land on the right step (e.g. after the GitHub OAuth round-trip).
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/v1/dashboard`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;

        const hasClaim = data?.state && data.state !== "no_client";
        const connected = Boolean(data?.github_connected);
        const repo = data?.github_repo as string | undefined;
        const claimDomain = data?.company?.domain ?? data?.audit?.domain;

        if (claimDomain && !prefillDomain) setDomain(claimDomain);
        if (hasClaim) setClaimed(true);
        if (connected) setGithubConnected(true);
        if (repo) setSelectedRepo(repo);

        // Advance to the furthest reachable step.
        if (connected) setStep(3);
        else if (hasClaim) setStep(2);
        else setStep(1);
      } catch {
        /* dashboard unavailable — stay on step 1 */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Fetch repos when we reach step 3 and are connected.
  useEffect(() => {
    if (step !== 3 || !githubConnected || !token) return;
    let cancelled = false;
    setReposLoading(true);
    setReposError(null);
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/v1/github/repos`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as { repos: Repo[] };
        if (!cancelled) setRepos(data.repos ?? []);
      } catch {
        if (!cancelled)
          setReposError("Couldn't load your repositories. Try reconnecting GitHub.");
      } finally {
        if (!cancelled) setReposLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [step, githubConnected, token]);

  /* ── Actions ─────────────────────────────────────────────────────────────── */

  async function handleClaim(e: React.FormEvent) {
    e.preventDefault();
    const raw = domain.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!raw || !token) return;

    setClaiming(true);
    setClaimError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/onboarding/claim`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          domain: raw,
          founder_name: founderName.trim() || undefined,
          founder_email: founderEmail.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `HTTP ${res.status}`);
      }
      setDomain(raw);
      setClaimed(true);
      setStep(2);
    } catch (err) {
      setClaimError(
        err instanceof Error ? err.message : "Couldn't claim that domain."
      );
    } finally {
      setClaiming(false);
    }
  }

  async function handleConnectGitHub() {
    if (!token) return;
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await fetch(
        `${BACKEND_URL}/api/v1/github/authorize-url`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { url } = (await res.json()) as { url: string };
      if (!url) throw new Error("No authorize URL returned.");
      window.location.href = url;
    } catch (err) {
      setConnectError(
        err instanceof Error ? err.message : "Couldn't start GitHub connect."
      );
      setConnecting(false);
    }
  }

  const handleSelectRepo = useCallback(
    async (repo: string) => {
      if (!token) return;
      setSelectedRepo(repo);
      setSavingRepo(true);
      setFixError(null);
      try {
        const res = await fetch(
          `${BACKEND_URL}/api/v1/onboarding/select-repo`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ repo }),
          }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch {
        setFixError("Couldn't save that repo selection.");
        setSelectedRepo(null);
      } finally {
        setSavingRepo(false);
      }
    },
    [token]
  );

  async function handleGenerateFix() {
    if (!token || !selectedRepo) return;
    setStartingFix(true);
    setFixError(null);
    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/fix/start`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ repo: selectedRepo }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = (await res.json()) as { job_id: string };
      router.push(`/fix/${job_id}`);
    } catch {
      setFixError("Couldn't start the fix. Try again.");
      setStartingFix(false);
    }
  }

  const filteredRepos = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, query]);

  /* ── Render ──────────────────────────────────────────────────────────────── */

  if (sessionStatus === "loading") {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2
          className="h-5 w-5 animate-spin"
          style={{ color: "var(--muted-foreground)" }}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-xl space-y-8">
      <div>
        <div className="eyebrow mb-1">Set up your product</div>
        <h1 className="font-display text-2xl font-bold">
          Get agent-ready in three steps
        </h1>
        <p
          className="mt-1.5 text-sm"
          style={{ color: "var(--muted-foreground)" }}
        >
          Claim your product, connect GitHub, and we&apos;ll open a fix PR for
          every failing dimension.
        </p>
      </div>

      <Stepper step={step} claimed={claimed} connected={githubConnected} />

      {/* ── Step 1 — Claim ─────────────────────────────────────────────── */}
      {step === 1 && (
        <Card>
          <div className="eyebrow mb-1">Step 1</div>
          <h2 className="font-display text-lg font-bold mb-1">
            Claim your product
          </h2>
          <p
            className="mb-5 text-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            Enter the domain you want to make agent-ready. We&apos;ll attach
            your existing audit if there is one.
          </p>

          <form onSubmit={handleClaim} className="space-y-4">
            <div>
              <label
                className="eyebrow mb-1.5 block text-[11px]"
                style={{ color: "var(--muted-foreground)" }}
              >
                Domain
              </label>
              <div className="relative">
                <span
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-xs select-none"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  https://
                </span>
                <input
                  type="text"
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  placeholder="yourproduct.com"
                  autoFocus
                  className={cn(
                    "h-10 w-full rounded border bg-surface-1 pl-16 pr-3 font-mono text-sm outline-none",
                    "transition-colors duration-100",
                    "focus:border-primary focus:ring-1 focus:ring-primary/40",
                    "placeholder:text-fg-subtle"
                  )}
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label
                  className="eyebrow mb-1.5 block text-[11px]"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  Founder name{" "}
                  <span style={{ color: "var(--fg-subtle)" }}>· optional</span>
                </label>
                <input
                  type="text"
                  value={founderName}
                  onChange={(e) => setFounderName(e.target.value)}
                  placeholder="Ada Lovelace"
                  className={cn(
                    "h-10 w-full rounded border bg-surface-1 px-3 text-sm outline-none",
                    "transition-colors duration-100",
                    "focus:border-primary focus:ring-1 focus:ring-primary/40",
                    "placeholder:text-fg-subtle"
                  )}
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
              <div>
                <label
                  className="eyebrow mb-1.5 block text-[11px]"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  Founder email{" "}
                  <span style={{ color: "var(--fg-subtle)" }}>· optional</span>
                </label>
                <input
                  type="email"
                  value={founderEmail}
                  onChange={(e) => setFounderEmail(e.target.value)}
                  placeholder="ada@yourproduct.com"
                  className={cn(
                    "h-10 w-full rounded border bg-surface-1 px-3 text-sm outline-none",
                    "transition-colors duration-100",
                    "focus:border-primary focus:ring-1 focus:ring-primary/40",
                    "placeholder:text-fg-subtle"
                  )}
                  style={{ borderColor: "var(--border)" }}
                />
              </div>
            </div>

            {claimError && (
              <div
                className="rounded border px-3 py-2 text-xs"
                style={{
                  borderColor: "oklch(0.53 0.22 20 / 0.3)",
                  background: "oklch(0.53 0.22 20 / 0.06)",
                  color: "oklch(0.53 0.22 20)",
                }}
              >
                {claimError}
              </div>
            )}

            <button
              type="submit"
              disabled={claiming || !domain.trim() || !token}
              className={cn(
                "group inline-flex h-10 w-full items-center justify-center gap-2 rounded text-sm font-medium",
                "transition-transform duration-100 active:scale-[0.99] disabled:opacity-50"
              )}
              style={{
                background: "var(--primary)",
                color: "var(--primary-foreground)",
              }}
            >
              {claiming ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  Continue
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                </>
              )}
            </button>
          </form>
        </Card>
      )}

      {/* ── Step 2 — Connect GitHub ────────────────────────────────────── */}
      {step === 2 && (
        <Card>
          <div className="eyebrow mb-1">Step 2</div>
          <h2 className="font-display text-lg font-bold mb-1">
            Connect GitHub
          </h2>
          <p
            className="mb-5 text-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            We need read/write access to the repo we&apos;ll open the fix PR
            against. You&apos;ll be redirected to GitHub and back.
          </p>

          {domain && (
            <div
              className="mb-5 flex items-center gap-2 rounded border px-3 py-2"
              style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
            >
              <Globe
                className="h-3.5 w-3.5 shrink-0"
                style={{ color: "var(--muted-foreground)" }}
              />
              <span className="font-mono text-xs">{domain}</span>
              <span
                className="ml-auto inline-flex items-center gap-1 text-[10px] uppercase tracking-wider"
                style={{ color: "oklch(0.52 0.17 152)" }}
              >
                <Check className="h-3 w-3" /> claimed
              </span>
            </div>
          )}

          {connectError && (
            <div
              className="mb-4 rounded border px-3 py-2 text-xs"
              style={{
                borderColor: "oklch(0.53 0.22 20 / 0.3)",
                background: "oklch(0.53 0.22 20 / 0.06)",
                color: "oklch(0.53 0.22 20)",
              }}
            >
              {connectError}
            </div>
          )}

          {githubConnected ? (
            <div className="space-y-4">
              <div
                className="flex items-center gap-2 rounded border px-3 py-2.5 text-sm"
                style={{
                  borderColor: "oklch(0.52 0.17 152 / 0.3)",
                  background: "oklch(0.52 0.17 152 / 0.04)",
                  color: "oklch(0.52 0.17 152)",
                }}
              >
                <Check className="h-4 w-4" />
                GitHub connected
              </div>
              <button
                onClick={() => setStep(3)}
                className="group inline-flex h-10 w-full items-center justify-center gap-2 rounded text-sm font-medium transition-transform duration-100 active:scale-[0.99]"
                style={{
                  background: "var(--primary)",
                  color: "var(--primary-foreground)",
                }}
              >
                Pick a repo
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </button>
            </div>
          ) : (
            <button
              onClick={handleConnectGitHub}
              disabled={connecting || !token}
              className={cn(
                "inline-flex h-10 w-full items-center justify-center gap-2 rounded text-sm font-medium",
                "transition-transform duration-100 active:scale-[0.99] disabled:opacity-50",
                "bg-foreground text-background"
              )}
            >
              {connecting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <Github className="h-4 w-4" />
                  Connect GitHub
                </>
              )}
            </button>
          )}
        </Card>
      )}

      {/* ── Step 3 — Pick a repo ───────────────────────────────────────── */}
      {step === 3 && (
        <Card>
          <div className="eyebrow mb-1">Step 3</div>
          <h2 className="font-display text-lg font-bold mb-1">
            Pick a repository
          </h2>
          <p
            className="mb-5 text-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            Choose the repo behind {domain || "your product"}. We&apos;ll open
            the fix PR here.
          </p>

          {/* Search */}
          <div className="relative mb-3">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
              style={{ color: "var(--muted-foreground)" }}
            />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search repositories…"
              className={cn(
                "h-10 w-full rounded border bg-surface-1 pl-9 pr-3 text-sm outline-none",
                "transition-colors duration-100",
                "focus:border-primary focus:ring-1 focus:ring-primary/40",
                "placeholder:text-fg-subtle"
              )}
              style={{ borderColor: "var(--border)" }}
            />
          </div>

          {/* Repo list */}
          <div
            className="overflow-hidden rounded border"
            style={{ borderColor: "var(--border)" }}
          >
            {reposLoading ? (
              <div className="flex items-center justify-center gap-2 py-10 text-xs"
                style={{ color: "var(--muted-foreground)" }}
              >
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading repositories…
              </div>
            ) : reposError ? (
              <div
                className="px-4 py-6 text-center text-xs"
                style={{ color: "oklch(0.53 0.22 20)" }}
              >
                {reposError}
              </div>
            ) : filteredRepos.length === 0 ? (
              <div
                className="px-4 py-6 text-center text-xs"
                style={{ color: "var(--muted-foreground)" }}
              >
                {repos.length === 0
                  ? "No repositories found on your GitHub account."
                  : "No repositories match your search."}
              </div>
            ) : (
              <div className="max-h-72 overflow-y-auto scrollbar-minimal">
                {filteredRepos.map((repo, i) => {
                  const selected = selectedRepo === repo.full_name;
                  return (
                    <button
                      key={repo.full_name}
                      onClick={() => handleSelectRepo(repo.full_name)}
                      disabled={savingRepo}
                      className="flex w-full items-center gap-3 border-b px-4 py-3 text-left last:border-b-0 transition-colors disabled:opacity-60"
                      style={{
                        borderColor: "var(--border)",
                        background: selected
                          ? "oklch(0.65 0.16 240 / 0.08)"
                          : i % 2 === 0
                            ? "var(--surface-1)"
                            : "var(--surface-2)",
                      }}
                    >
                      <span
                        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border"
                        style={{
                          borderColor: selected
                            ? "var(--primary)"
                            : "var(--border-strong)",
                          background: selected ? "var(--primary)" : "transparent",
                        }}
                      >
                        {selected && (
                          <Check
                            className="h-2.5 w-2.5"
                            style={{ color: "var(--primary-foreground)" }}
                          />
                        )}
                      </span>
                      <span className="font-mono text-sm flex-1 truncate">
                        {repo.full_name}
                      </span>
                      {repo.private && (
                        <span
                          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider"
                          style={{ color: "var(--fg-subtle)" }}
                        >
                          <Lock className="h-3 w-3" /> private
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {fixError && (
            <div
              className="mt-4 rounded border px-3 py-2 text-xs"
              style={{
                borderColor: "oklch(0.53 0.22 20 / 0.3)",
                background: "oklch(0.53 0.22 20 / 0.06)",
                color: "oklch(0.53 0.22 20)",
              }}
            >
              {fixError}
            </div>
          )}

          {/* Generate the fix */}
          <button
            onClick={handleGenerateFix}
            disabled={!selectedRepo || savingRepo || startingFix || !token}
            className={cn(
              "group mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded text-sm font-medium",
              "transition-transform duration-100 active:scale-[0.99] disabled:opacity-50"
            )}
            style={{
              background: "var(--primary)",
              color: "var(--primary-foreground)",
            }}
          >
            {startingFix || savingRepo ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                Generate the fix
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </>
            )}
          </button>
        </Card>
      )}
    </div>
  );
}
