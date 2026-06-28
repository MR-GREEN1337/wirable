"use client";

// Cursor-based polling of GET /api/v1/run/{id}/state.
//
// WHY: the Next.js standalone proxy buffers SSE responses, so EventSource frames
// arrive in one burst at completion instead of live. A plain-JSON state endpoint
// passes cleanly through the proxy, so we poll it every ~500ms, advance a cursor,
// and dispatch each new event exactly as the SSE handler would have. This is what
// makes the live run cockpit play like a real streaming video of the agent.
//
// RELIABILITY: the loop polls until `done`. Transient fetch failures (proxy
// hiccup, cold backend, network blip) must NOT kill the loop — they're surfaced
// via onError but we keep polling (a short backoff) so late frames after a burst
// still render. The cursor is only advanced on a successful response, so nothing
// is skipped across a failed tick.

import { useEffect, useRef } from "react";
import { BACKEND_URL, type RunEvent } from "@/lib/run-events";

// ~500ms cadence: tight enough that per-step frames feel live, loose enough not
// to hammer the proxy. On a transient error we back off slightly before retry.
const POLL_MS = 500;
const ERROR_BACKOFF_MS = 900;

type StateResponse = {
  events: RunEvent[];
  cursor: number;
  done: boolean;
};

export function useRunPoll(
  runId: string | null | undefined,
  onEvent: (ev: RunEvent) => void,
  opts?: { onError?: () => void; enabled?: boolean; epoch?: number },
) {
  // Keep the callback in a ref so the poll loop never re-subscribes on re-render
  // (we want one stable loop per runId).
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onErrorRef = useRef(opts?.onError);
  onErrorRef.current = opts?.onError;

  const enabled = opts?.enabled ?? true;
  // Bumping `epoch` re-runs the effect → a fresh poll session that replays the
  // full history from cursor=0 (the consumer's reducer must be idempotent). Used
  // to resume streaming after the run's terminal event when more events follow
  // (e.g. the proxy FIX flow appending fix_pr / verify to the same bus).
  const epoch = opts?.epoch ?? 0;

  useEffect(() => {
    if (!runId || !enabled) return;

    let cursor = 0;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (stopped) return;
      let nextDelay = POLL_MS;
      try {
        const res = await fetch(
          `${BACKEND_URL}/api/v1/run/${runId}/state?cursor=${cursor}`,
          { cache: "no-store" },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as StateResponse;
        // Only advance the cursor on success → a failed tick never skips events.
        cursor = data.cursor;
        for (const ev of data.events) {
          if (stopped) return;
          onEventRef.current(ev);
        }
        if (data.done) {
          stopped = true;
          return;
        }
      } catch {
        // Transient failure: surface it but DO NOT stop the loop. Back off a
        // touch and retry so late frames still arrive once the backend recovers.
        onErrorRef.current?.();
        nextDelay = ERROR_BACKOFF_MS;
      }
      if (!stopped) timer = setTimeout(tick, nextDelay);
    }

    // Kick immediately so the first frames land without a 700ms wait.
    tick();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, enabled, epoch]);
}
