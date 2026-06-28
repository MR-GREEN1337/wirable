from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    DAYTONA_API_KEY: str = ""
    DAYTONA_SERVER_URL: str = "https://app.daytona.io"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_API_KEYS: str = ""  # comma-separated pool; rotated across requests
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    INTERNAL_SECRET: str = ""
    # Public base URL of the Wirable web app (used for absolute links).
    APP_BASE_URL: str = "http://localhost:3000"
    # GitHub OAuth — used to connect a user's repo and open the FIX PR.
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    # Optional shared secret for verifying inbound GitHub webhook deliveries
    # (X-Hub-Signature-256). When empty, webhook signatures are not enforced.
    GITHUB_WEBHOOK_SECRET: str = ""

    # --- Launch gating (entitlements / billing) ---------------------------
    # Require an authenticated account to start a run. Default ON — every run
    # burns real Claude + sandbox dollars, so anonymous runs are blocked. Flip
    # to False as a safety toggle to restore the old anonymous behaviour.
    WIRABLE_REQUIRE_AUTH: bool = True
    # Free runs each account gets before it must redeem a code or upgrade.
    WIRABLE_FREE_RUNS: int = 2
    # Max runs that may do their heavy N-sandbox fan-out concurrently. A traffic
    # spike (e.g. a Product Hunt launch) queues excess runs on a semaphore rather
    # than exhausting Daytona quota + the Claude key pool. Excess runs WAIT (the
    # UI shows a "queued" line), they do not error.
    WIRABLE_MAX_CONCURRENT_RUNS: int = 4
    # Comma-separated judge/internal access codes that grant unlimited access.
    WIRABLE_ACCESS_CODES: str = ""        # bonus codes: grant a limited allowance
    WIRABLE_UNLIMITED_CODES: str = ""     # judge/internal codes: grant unlimited
    WIRABLE_BONUS_RUNS: int = 10          # runs granted by a bonus code
    # Stripe — billing activates only when both the secret key and a price id
    # are set; the webhook signature is verified only when the secret is set.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PRICE_ID: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # --- Email/password signup ------------------------------------------
    # Cloudflare Turnstile secret — when empty, the bot check is bypassed
    # (dev) and a warning is logged. Resend powers the welcome email; when
    # the API key is empty, welcome email is a no-op.
    TURNSTILE_SECRET_KEY: str = ""
    RESEND_API_KEY: str = ""
    WIRABLE_EMAIL_FROM: str = "Wirable <onboarding@resend.dev>"

    # --- Observability ----------------------------------------------------
    # Sentry error tracking. No-op when SENTRY_DSN is empty: Sentry is never
    # initialised, so a missing DSN (or a missing sentry-sdk package) never
    # affects startup.
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "production"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def anthropic_keys(self) -> list[str]:
        """Resolve the Claude key pool.

        Splits ANTHROPIC_API_KEYS on commas (dropping blanks). Falls back to the
        single ANTHROPIC_API_KEY when the pool is empty, else returns []. Never
        raises — an empty list means "no Claude keys configured", and every
        LLM/arbiter path degrades gracefully in that case.
        """
        pooled = [k.strip() for k in self.ANTHROPIC_API_KEYS.split(",") if k.strip()]
        if pooled:
            return pooled
        if self.ANTHROPIC_API_KEY.strip():
            return [self.ANTHROPIC_API_KEY.strip()]
        return []


settings = Settings()
