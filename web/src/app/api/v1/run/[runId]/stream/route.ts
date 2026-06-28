// SSE proxy as a Route Handler (NOT a next.config rewrite): rewrites buffer the
// whole response, which kills live streaming. A Route Handler that returns the
// upstream ReadableStream pipes events through in real time. Route handlers take
// precedence over afterFiles rewrites, so only the live stream path is special-cased.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const fetchCache = "force-no-store";

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  const upstream = await fetch(`${BACKEND}/api/v1/run/${runId}/stream`, {
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
  });

  // Explicitly pump the upstream reader and flush each chunk immediately —
  // passing upstream.body directly let Next buffer the whole SSE response.
  const reader = upstream.body!.getReader();
  const stream = new ReadableStream({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch {
        controller.close();
      }
    },
    cancel() {
      reader.cancel().catch(() => {});
    },
  });

  return new Response(stream, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      "Content-Encoding": "none",
    },
  });
}
