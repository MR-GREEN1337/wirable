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
    SCOUT_ENABLED: bool = False
    SCOUT_INTERVAL_MINUTES: int = 30
    SCOUT_BATCH_SIZE: int = 3
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    # Where GitHub redirects back after OAuth consent. Defaults to
    # f"{REPORT_BASE_URL}/github" (resolved in the authorize-url endpoint).
    GITHUB_REDIRECT_URI: str = ""
    UNIPILE_DSN: str = ""
    UNIPILE_API_KEY: str = ""
    UNIPILE_ACCOUNT_ID: str = ""
    SENDING_EMAIL: str = ""
    INTERNAL_SECRET: str = ""
    REPORT_BASE_URL: str = "http://localhost:3000"

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
