from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.router import router

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
