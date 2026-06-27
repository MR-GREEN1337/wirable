"use client";

import { useState, useEffect, useRef, use } from "react";
import { AuditTerminal, type TerminalLine, type AuditShot } from "@/components/AuditTerminal";
import { ScoreCard } from "@/components/ScoreCard";
import { ArrowLeft, GitPullRequest, CheckCircle2 } from "lucide-react";
import Link from "next/link";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

interface FixStreamEvent {
  type: "line" | "pr_open" | "score" | "done" | "error" | "status" | "screenshot";
  line?: TerminalLine;
  ok?: boolean;
  msg?: string;
  seq?: number;
  caption?: string;
  dimension?: string;
  url?: string;
  image?: string;
  pr_url?: string;
  pr_number?: number;
  pr_files?: string[];
  score?: number;
  confidence?: number;
  before_score?: number;
  after_score?: number;
  before_dims?: Record<string, { passed: boolean; needs_live?: boolean }>;
  after_dims?: Record<string, { passed: boolean; needs_live?: boolean }>;
  message?: string;
  domain?: string;
}

type FixPhase =
  | "connecting"
  | "running"
  | "pr_open"
  | "verifying"
  | "done"
  | "error";

export default function FixStreamPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);

  const [domain, setDomain] = useState("");
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [screenshots, setScreenshots] = useState<AuditShot[]>([]);
  const [phase, setPhase] = useState<FixPhase>("connecting");
  const [error, setError] = useState<string | null>(null);

  // PR state
  const [prUrl, setPrUrl] = useState<string | undefined>();
  const [prNumber, setPrNumber] = useState<number | undefined>();
  const [prFiles, setPrFiles] = useState<string[]>([]);

  // Score state
  const [beforeScore, setBeforeScore] = useState<number | undefined>();
  const [afterScore, setAfterScore] = useState<number | undefined>();
  const [beforeDims, setBeforeDims] = useState<
    Record<string, { passed: boolean; needs_live?: boolean }>
  >({});
  const [afterDims, setAfterDims] = useState<
    Record<string, { passed: boolean; needs_live?: boolean }>
  >({});
  const [verifyScore, setVerifyScore] = useState<number | undefined>();
  const [verifyConfidence, setVerifyConfidence] = useState<
    number | undefined
  >();

  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const url = `${BACKEND_URL}/api/v1/fix/${jobId}/stream`;
    const es = new EventSource(url);
    esRef.current = es;
    setPhase("running");

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as FixStreamEvent;

        if (data.type === "status" && data.domain) {
          setDomain(data.domain);
        }

        if (data.type === "line" && data.msg) {
          const msg = data.msg;
          setLines((prev) => [...prev, { type: data.ok !== false ? "ok" : "err", msg }]);
        }

        if (data.type === "screenshot" && data.image && data.seq !== undefined) {
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
        }

        if (data.type === "pr_open") {
          setPrUrl(data.pr_url);
          setPrNumber(data.pr_number);
          setPrFiles(data.pr_files ?? []);
          if (data.before_score !== undefined) setBeforeScore(data.before_score);
          if (data.after_score !== undefined) setAfterScore(data.after_score);
          if (data.before_dims) setBeforeDims(data.before_dims);
          if (data.after_dims) setAfterDims(data.after_dims);
          setPhase("pr_open");
        }

        if (data.type === "score" && data.score !== undefined) {
          // Post-fix verification score
          setVerifyScore(data.score);
          setVerifyConfidence(data.confidence);
          setPhase("done");
          es.close();
        }

        if (data.type === "done") {
          setPhase("done");
          es.close();
        }

        if (data.type === "error") {
          setError(data.message ?? "Fix job failed");
          setPhase("error");
          es.close();
        }
      } catch {
        // malformed frame
      }
    };

    es.onerror = () => {
      setPhase("error");
      setError("Stream connection lost");
      es.close();
    };

    return () => es.close();
  }, [jobId]);

  const phaseLabel: Record<FixPhase, string> = {
    connecting: "Connecting…",
    running: "Generating fix",
    pr_open: "PR open — awaiting merge",
    verifying: "Verifying",
    done: "Done",
    error: "Error",
  };

  const phaseColor: Record<FixPhase, string> = {
    connecting: "var(--muted-foreground)",
    running: "oklch(0.52 0.17 152)",
    pr_open: "var(--primary)",
    verifying: "oklch(0.68 0.18 62)",
    done: "oklch(0.52 0.17 152)",
    error: "oklch(0.53 0.22 20)",
  };

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-3">
        <Link
          href="/dashboard"
          className="cn-hover inline-flex items-center gap-1.5 text-xs"
          style={{ color: "var(--muted-foreground)" }}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Dashboard
        </Link>
        <span style={{ color: "var(--muted-foreground)" }}>/</span>
        <span className="text-xs">Fix job</span>
        {domain && (
          <>
            <span style={{ color: "var(--muted-foreground)" }}>/</span>
            <span className="font-mono text-xs">{domain}</span>
          </>
        )}
      </div>

      {/* Phase indicator */}
      <div className="flex items-center gap-3">
        <div className="eyebrow">Fix stream</div>
        <div className="flex items-center gap-1.5">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: phaseColor[phase],
              animation:
                phase === "running" || phase === "verifying"
                  ? "cursor-blink 1.2s ease-in-out infinite"
                  : undefined,
            }}
          />
          <span
            className="text-xs uppercase tracking-wider"
            style={{ color: "var(--muted-foreground)" }}
          >
            {phaseLabel[phase]}
          </span>
        </div>
      </div>

      {error && (
        <div
          className="rounded border px-4 py-3 text-sm"
          style={{
            borderColor: "oklch(0.53 0.22 20 / 0.3)",
            background: "oklch(0.53 0.22 20 / 0.06)",
            color: "oklch(0.53 0.22 20)",
          }}
        >
          {error}
        </div>
      )}

      {/* Terminal — shown during running phase */}
      {lines.length > 0 && (
        <AuditTerminal
          domain={domain}
          lines={lines}
          score={verifyScore}
          confidence={verifyConfidence}
          screenshots={screenshots}
        />
      )}

      {/* ScoreCard — shown once PR is open */}
      {(phase === "pr_open" || phase === "done") &&
        beforeScore !== undefined &&
        afterScore !== undefined && (
          <div>
            <div className="eyebrow mb-3">Expected improvement</div>
            <ScoreCard
              beforeScore={beforeScore}
              afterScore={afterScore}
              beforeDims={beforeDims}
              afterDims={afterDims}
              prUrl={prUrl}
              prNumber={prNumber}
              prFiles={prFiles}
            />
          </div>
        )}

      {/* Done state */}
      {phase === "done" && verifyScore !== undefined && (
        <div
          className="rounded border p-6 text-center"
          style={{
            borderColor: "oklch(0.52 0.17 152 / 0.3)",
            background: "oklch(0.52 0.17 152 / 0.04)",
          }}
        >
          <CheckCircle2
            className="mx-auto h-8 w-8 mb-3"
            style={{ color: "oklch(0.52 0.17 152)" }}
          />
          <div
            className="eyebrow mb-2"
            style={{ color: "oklch(0.52 0.17 152)" }}
          >
            Verified
          </div>
          <div className="font-display text-5xl font-bold data mb-2"
            style={{ color: "oklch(0.52 0.17 152)" }}
          >
            {verifyScore}
          </div>
          <div
            className="text-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            Post-fix score confirmed at{" "}
            {verifyConfidence !== undefined
              ? `${Math.round(verifyConfidence * 100)}% confidence`
              : "high confidence"}
          </div>
          <div className="mt-6">
            <Link
              href="/dashboard"
              className="group inline-flex items-center gap-2 rounded-xl bg-foreground px-5 py-2.5 text-sm font-medium text-background transition-transform hover:-translate-y-px"
            >
              Back to dashboard
            </Link>
          </div>
        </div>
      )}

      {/* PR open state — prompt user to merge */}
      {phase === "pr_open" && prUrl && (
        <div
          className="rounded border p-4 flex items-center justify-between gap-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
        >
          <div className="flex items-start gap-3">
            <GitPullRequest
              className="h-4 w-4 mt-0.5 shrink-0"
              style={{ color: "var(--primary)" }}
            />
            <div>
              <div className="text-sm font-medium">PR is open on GitHub</div>
              <div
                className="text-xs mt-0.5"
                style={{ color: "var(--muted-foreground)" }}
              >
                Review and merge the changes. We&apos;ll automatically verify
                and update your score.
              </div>
            </div>
          </div>
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium"
            style={{
              background: "var(--primary)",
              color: "var(--primary-foreground)",
            }}
          >
            View PR
          </a>
        </div>
      )}
    </div>
  );
}
