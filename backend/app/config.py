from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Required fields have no default: boot fails loudly instead of running insecurely."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"

    mongo_uri: str
    mongo_db_name: str = "labledger"

    jwt_secret: str
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 14
    # A session with no request for this long is ended server-side. Short by
    # default: the threat is a signed-in workstation left on a ward, and the
    # cost of being wrong is one sign-in.
    session_idle_timeout_min: int = 30
    # Days an account may keep reaching a record it does not own before a
    # second factor becomes mandatory. Counted from its first such access, not
    # from when the grant was issued, so turning the policy on does not lock
    # out people whose grants predate it. 0 disables enforcement entirely.
    mfa_grace_days: int = 7
    field_encryption_key: str  # base64, 32 bytes

    # Optional: cascade degrades to the review queue when absent.
    gemini_api_key: str | None = None
    # Pinned, not "-latest": the model version is recorded in every mapping's
    # provenance, so it must not change under us silently.
    gemini_model: str = "gemini-3.5-flash"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_callback_url: str = "http://localhost:8000/api/auth/google/callback"

    redis_url: str
    arq_queue_name: str = "labledger:jobs"
    # Run the arq worker inside the API process instead of as its own service.
    # Off by default, because a separate worker is the right shape: extraction
    # is CPU-bound and competing with request handling for the event loop is a
    # real cost. It exists because Render has no free instance type for a
    # background worker, and one process on a free web service is the
    # difference between uploads processing and not. See render.yaml.
    run_worker_in_api: bool = False

    llm_confidence_floor: float = 0.80
    log_level: str = "info"

    max_upload_bytes: int = 25 * 1024 * 1024

    # EmailJS, called server-side with the private key. Absent keys disable
    # sending rather than failing: a dev machine without them still runs, and
    # the reset flow degrades to "no email arrives" rather than to a 500.
    emailjs_service_id: str | None = None
    emailjs_public_key: str | None = None
    emailjs_private_key: str | None = None
    emailjs_template_reset: str | None = None
    emailjs_template_invite: str | None = None
    password_reset_ttl_min: int = 30

    @property
    def mail_configured(self) -> bool:
        """True when a real send is possible."""
        return bool(self.emailjs_service_id and self.emailjs_public_key
                    and self.emailjs_private_key)

    @property
    def is_test(self) -> bool:
        """True under pytest. Rate limits are disabled so tests do not share one IP bucket."""
        return self.env == "test"

    @property
    def is_prod(self) -> bool:
        """True when running with production hardening enabled."""
        return self.env == "production"


settings = Settings()  # type: ignore[call-arg]
