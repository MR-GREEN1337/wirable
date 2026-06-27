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
