"use client";

import { useState, useEffect, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { AuditTerminal, type TerminalLine, type AuditShot } from "@/components/AuditTerminal";
import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

interface StreamEvent {
  type: "line" | "score" | "error" | "status" | "done" | "screenshot";
  line?: TerminalLine;
  ok?: boolean;
  msg?: string;
  score?: number;
  confidence?: number;
  report_id?: string;
  domain?: string;
  message?: string;
  seq?: number;
  caption?: string;
  dimension?: string;
  url?: string;
  image?: string;
}

export default function AuditStreamPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const router = useRouter();

  const [domain, setDomain] = useState(jobId); // placeholder until we get domain from stream
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [screenshots, setScreenshots] = useState<AuditShot[]>([]);
  const [score, setScore] = useState<number | undefined>();
  const [confidence, setConfidence] = useState<number | undefined>();
  const [reportId, setReportId] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<
    "connecting" | "running" | "done" | "error"
  >("connecting");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const url = `${BACKEND_URL}/api/v1/audit/${jobId}/stream`;
    const es = new EventSource(url);
    esRef.current = es;
    setStatus("running");

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as StreamEvent;

        if (data.type === "line" && data.msg) {
          const msg = data.msg;
          setLines((prev) => [...prev, { type: data.ok !== false ? "ok" : "err", msg }]);
        }

        if (data.type === "status" && data.domain) {
          setDomain(data.domain);
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

        if (data.type === "score" && data.score !== undefined) {
          setScore(data.score);
          setConfidence(data.confidence);
          if (data.report_id) setReportId(data.report_id);
          setStatus("done");
          es.close();
        }

        if (data.type === "error") {
          setError(data.message ?? "Audit failed");
          setStatus("error");
          es.close();
        }
      } catch {
        // malformed frame
      }
    };

    es.onerror = () => {
      if (status === "running") {
        setError("Stream disconnected — check job status");
        setStatus("error");
      }
      es.close();
    };

    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  return (
    <div>
      {/* Breadcrumb */}
      <div className="mb-6 flex items-center gap-3">
        <Link
          href="/dashboard"
          className="cn-hover inline-flex items-center gap-1.5 text-xs"
          style={{ color: "var(--muted-foreground)" }}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Dashboard
        </Link>
        <span style={{ color: "var(--muted-foreground)" }}>/</span>
        <span className="font-mono text-xs">{domain}</span>
      </div>

      <div className="mb-4 flex items-center gap-3">
        <div className="eyebrow">Live audit</div>
        <div className="flex items-center gap-1.5">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background:
                status === "running"
                  ? "oklch(0.52 0.17 152)"
                  : status === "error"
                    ? "oklch(0.53 0.22 20)"
                    : status === "done"
                      ? "oklch(0.52 0.17 152)"
                      : "var(--muted-foreground)",
              animation:
                status === "running"
                  ? "cursor-blink 1.2s ease-in-out infinite"
                  : undefined,
            }}
          />
          <span
            className="text-xs uppercase tracking-wider"
            style={{ color: "var(--muted-foreground)" }}
          >
            {status === "connecting"
              ? "Connecting…"
              : status === "running"
                ? "Running"
                : status === "done"
                  ? "Complete"
                  : "Error"}
          </span>
        </div>
      </div>

      {error && (
        <div
          className="mb-4 rounded border px-4 py-3 text-sm"
          style={{
            borderColor: "oklch(0.53 0.22 20 / 0.3)",
            background: "oklch(0.53 0.22 20 / 0.06)",
            color: "oklch(0.53 0.22 20)",
          }}
        >
          {error}
        </div>
      )}

      <AuditTerminal
        domain={domain}
        lines={lines}
        score={score}
        confidence={confidence}
        screenshots={screenshots}
        className="w-full"
      />

      {/* Actions when done */}
      {status === "done" && reportId && (
        <div
          className="mt-4 flex items-center gap-3 rounded border p-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
        >
          <div className="flex-1">
            <div className="text-sm font-medium">Audit complete</div>
            <div
              className="text-xs mt-0.5"
              style={{ color: "var(--muted-foreground)" }}
            >
              View the full report or connect your repo to get a fix PR.
            </div>
          </div>
          <Link
            href={`/report/${reportId}`}
            className="group inline-flex items-center gap-1.5 text-xs font-medium"
            style={{ color: "var(--primary)" }}
          >
            Full report
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
          <Link
            href="/dashboard"
            className="group inline-flex items-center gap-1.5 rounded-xl bg-foreground px-4 py-2 text-xs font-medium text-background transition-transform hover:-translate-y-px"
          >
            Dashboard
          </Link>
        </div>
      )}

      {status === "error" && (
        <div className="mt-4">
          <Link
            href="/dashboard"
            className="cn-hover text-sm"
            style={{ color: "var(--primary)" }}
          >
            Back to dashboard
          </Link>
        </div>
      )}
    </div>
  );
}
