from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.router import router
from .core.config import settings

# --- Sentry (no-op when SENTRY_DSN is unset) ------------------------------
# Guarded so a missing sentry-sdk package never breaks startup, and only
# initialised when a DSN is configured. The FastAPI integration (pulled in
# by sentry-sdk[fastapi]) auto-captures unhandled errors once init runs.
if settings.SENTRY_DSN:
    try:
        import sentry_sdk

        def _drop_expected_http(event, hint):
            # Intended client/billing responses (402 upgrade, 403, 404, 429,
            # 503 billing-not-configured) are control flow, not bugs — don't
            # let them pollute the issue stream. Only 5xx server faults pass.
            exc = (hint or {}).get("exc_info")
            if exc:
                err = exc[1]
                code = getattr(err, "status_code", None)
                if isinstance(code, int) and code < 500:
                    return None
            return event

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT or "production",
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_drop_expected_http,
        )
    except Exception:  # pragma: no cover - never let observability break boot
        pass

app = FastAPI(
    title="Wirable API",
    version="0.1.0",
    description=(
        "Wirable tests whether an AI agent can complete real workflows on any "
        "platform, scores it across 6 deterministic dimensions, then generates "
        "and hosts an MCP proxy that fixes the semantic breakage."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://wirable.dev",
        "https://app.wirable.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/", tags=["ops"])
async def root():
    return {"service": "Wirable API", "docs": "/docs"}
