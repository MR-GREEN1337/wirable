"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { AuditTerminal, type TerminalLine, type AuditShot } from "@/components/AuditTerminal";
import { CtaButton } from "@/components/CtaButton";
import { cn } from "@/lib/utils";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export function HeroAudit() {
  const router = useRouter();
  const [domain, setDomain] = useState("");
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [screenshots, setScreenshots] = useState<AuditShot[]>([]);
  const [score, setScore] = useState<number | undefined>();
  const [confidence, setConfidence] = useState<number | undefined>();
  const [reportId, setReportId] = useState<string | undefined>();
  const [auditedDomain, setAuditedDomain] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  async function runAudit(e: React.FormEvent) {
    e.preventDefault();
    const raw = domain.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (!raw) return;

    setLines([]);
    setScreenshots([]);
    setScore(undefined);
    setConfidence(undefined);
    setReportId(undefined);
    setAuditedDomain(raw);
    setError(null);
    setRunning(true);

    try {
      // Kick off the audit job
      const res = await fetch(`${BACKEND_URL}/api/v1/audit/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: raw }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `HTTP ${res.status}`);
      }

      const { job_id } = await res.json() as { job_id: string };

      // Connect SSE stream
      const es = new EventSource(`${BACKEND_URL}/api/v1/audit/${job_id}/stream`);
      esRef.current = es;

      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as {
            type?: string;
            line?: TerminalLine;
            ok?: boolean;
            msg?: string;
            score?: number;
            confidence?: number;
            report_id?: string;
            seq?: number;
            caption?: string;
            dimension?: string;
            url?: string;
            image?: string;
          };

          if (data.type === "line" && data.msg) {
            const msg = data.msg;
            setLines((prev) => [...prev, { type: data.ok !== false ? "ok" : "err", msg }]);
          } else if (data.type === "screenshot" && data.image && data.seq !== undefined) {
            const shot: AuditShot = {
              seq: data.seq,
              caption: data.caption ?? "",
              dimension: data.dimension,
              url: data.url,
              image: data.image,
            };
            setScreenshots((prev) =>
              prev.some((s) => s.seq === shot.seq) ? prev : [...prev, shot]
            );
          } else if (data.type === "score" && data.score !== undefined) {
            setScore(data.score);
            setConfidence(data.confidence);
            if (data.report_id) setReportId(data.report_id);
            es.close();
            setRunning(false);
            if (data.report_id) {
              // Navigate to the full report after a beat. The fallback CTA
              // below covers the case where navigation is blocked/cancelled.
              setTimeout(() => router.push(`/report/${data.report_id}`), 1500);
            }
          } else if (data.type === "error") {
            setError("Audit failed — check the domain and try again.");
            es.close();
            setRunning(false);
          }
        } catch {
          // malformed event, skip
        }
      };

      es.onerror = () => {
        setError("Connection lost. Please try again.");
        es.close();
        setRunning(false);
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setRunning(false);
    }
  }

  return (
    <div className="w-full max-w-2xl space-y-4">
      {/* Domain input */}
      <form onSubmit={runAudit} className="flex gap-2">
        <div className="relative flex-1">
          <span
            className="absolute left-3 top-1/2 -translate-y-1/2 font-mono text-xs select-none"
            style={{ color: "var(--muted-foreground)" }}
          >
            https://
          </span>
          <input
            type="text"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="yourproduct.com"
            disabled={running}
            className={cn(
              "h-10 w-full rounded border bg-surface-1 pl-16 pr-3 font-mono text-sm outline-none",
              "transition-colors duration-100",
              "focus:border-primary focus:ring-1 focus:ring-primary/40",
              "placeholder:text-fg-subtle disabled:opacity-60"
            )}
            style={{ borderColor: "var(--border)" }}
          />
        </div>
        <CtaButton
          type="submit"
          disabled={running || !domain.trim()}
          size="sm"
        >
          {running ? "Running…" : "Audit"}
        </CtaButton>
      </form>

      {/* Error */}
      {error && (
        <div
          className="rounded border px-3 py-2 text-xs"
          style={{
            borderColor: "oklch(0.53 0.22 20 / 0.3)",
            background: "oklch(0.53 0.22 20 / 0.06)",
            color: "oklch(0.53 0.22 20)",
          }}
        >
          {error}
        </div>
      )}

      {/* Terminal — only visible once started */}
      {(lines.length > 0 || running) && (
        <AuditTerminal
          domain={domain}
          lines={lines}
          score={score}
          confidence={confidence}
          screenshots={screenshots}
          className="w-full"
        />
      )}

      {/* Next-action CTA — never leave the user staring at a finished terminal */}
      {score !== undefined && !running && (
        <div className="flex flex-wrap items-center gap-3">
          {reportId && (
            <Link
              href={`/report/${reportId}`}
              className="group inline-flex items-center gap-1.5 rounded border px-4 py-2 text-sm font-medium transition-colors"
              style={{
                borderColor: "var(--border-strong)",
                background: "var(--surface-1)",
                color: "var(--foreground)",
              }}
            >
              See full report
              <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </Link>
          )}
          <Link
            href={`/signin?callbackUrl=${encodeURIComponent(
              `/onboarding?domain=${auditedDomain}`
            )}`}
            className="group inline-flex items-center gap-1.5 rounded px-4 py-2 text-sm font-medium transition-transform hover:-translate-y-px"
            style={{
              background: "var(--primary)",
              color: "var(--primary-foreground)",
            }}
          >
            Fix this
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      )}
    </div>
  );
}
