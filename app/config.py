from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Supabase (handoff Section 0, v2.3) — replaces the old raw DATABASE_URL.
    # SUPABASE_URL is the project's REST/API URL, e.g. https://<project-ref>.supabase.co — kept for any
    # future PostgREST/Storage calls, not used to build the SQLAlchemy DSN below.
    supabase_url: str = "https://changeme.supabase.co"
    # SUPABASE_SERVICE_ROLE_KEY bypasses RLS; correct for server-side use since auth is our own JWT layer,
    # not Supabase Auth. This is an API key, NOT the Postgres password — never used to build database_url.
    supabase_service_role_key: str = "change-me-in-production"
    # Required: Supabase's own Postgres connection string (Project Settings > Database > Connection string).
    # The API key above cannot substitute for this — Postgres auth uses a separate DB password set at
    # project creation. Use the "Session pooler" or "Transaction pooler" variant, not the direct
    # db.<project-ref>.supabase.co host — that host is IPv6-only and unreachable from most IPv4 networks.
    supabase_db_url: str

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 43200  # 30 days — handoff Section 4 Auth recommendation, no refresh token needed

    # Google SSO (handoff Section 0/4, v1.1) — the only authorized sign-in.
    authorized_owner_email: str = "owner@example.com"
    google_client_id: str = "change-me.apps.googleusercontent.com"

    # GCS Bucket Name (v3.50)
    gcs_bucket_name: str = "oura-studio-prod-bucket"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def authorized_owner_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.authorized_owner_email.split(",") if e.strip()}

    @property
    def database_url(self) -> str:
        return self.supabase_db_url


settings = Settings()
