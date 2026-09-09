from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OpenSearch -------------------------------------------------------
    opensearch_url: str = "http://localhost:9200"
    opensearch_username: str = ""
    opensearch_password: str = ""
    opensearch_verify_ssl: bool = False
    opensearch_log_index: str = "logintel-logs-*"
    opensearch_event_index: str = "logintel-events-*"
    opensearch_investigation_index: str = "logintel-investigations"
    opensearch_timeout: float = 30.0


    # --- Model backend ----------------------------------------------------
    # The model is an external service either way: a local Ollama on the host, or
    # a hosted API over the network. Local is the default — nothing leaves the
    # machine, and an investigation that makes eight tool calls costs nothing.
    #   ollama    -> OLLAMA_BASE_URL / OLLAMA_MODEL
    #   openai    -> any OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter,
    #                Together, vLLM, LM Studio): LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
    #   anthropic -> the Messages API: LLM_MODEL / LLM_API_KEY
    llm_provider: str = "ollama"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout: float = 180.0
    llm_temperature: float = 0.0
    llm_max_output_tokens: int = 2048

    # Transient-fault retries. A hosted endpoint rate-limits and sheds load, and
    # without these a single 429 or 503 ends an investigation that had already
    # done all of its expensive work. Measured against Gemini's free tier, one
    # call in six came back 503, so an eight-step run would rarely survive.
    # How many tokens the prompt may occupy, when the provider does not publish
    # it and the model is not one of the names in app/llm/factory.py. Used only
    # to show a utilisation figure beside an investigation; leaving it at 0
    # reports the prompt size with the window marked unknown, which is better
    # than a percentage measured against a ceiling nobody verified.
    llm_context_window: int = 0

    # Prompt caching. The system prompt, the tool schema and the evidence header
    # are byte-identical across every step of one investigation and across the
    # investigations in a thread, so the provider can be told to keep them
    # rather than re-reading them eight times. On Anthropic this is an explicit
    # `cache_control` marker; on OpenAI-compatible endpoints it happens
    # automatically as long as the prompt *prefix* stays stable, which is what
    # the ReAct transcript is built to preserve.
    llm_prompt_caching: bool = True

    # How long Gemini keeps the cached system prompt. It is the same 2,082
    # tokens for the life of the process, so this only decides how often the
    # cache is recreated — long enough to span a working session, short enough
    # that a changed tool schema cannot be served from a stale copy for a whole
    # day. Ignored on a key without cached-content quota, which is every
    # free-tier key.
    gemini_cache_ttl_seconds: int = 3600
    # Below this, a prompt is not worth caching and Gemini will not cache it
    # anyway — explicit cached content has a minimum size, and asking to cache
    # the planner's twelve-token instruction earns an INVALID_ARGUMENT. Checked
    # locally so that rejection never happens: it is indistinguishable, at the
    # HTTP layer, from the quota refusal that *should* switch caching off.
    gemini_cache_min_tokens: int = 1024
    # How long to stop asking after the API refuses to create a cache. Measured
    # behaviour: the same key returned `limit=0` twice and then succeeded, so
    # the refusal is a quota window rather than a property of the account.
    # Giving up permanently would cost every later run its caching over one bad
    # minute; retrying every call would pay a rejected round trip per request.
    gemini_cache_retry_seconds: int = 900

    llm_retry_attempts: int = 3
    llm_retry_base_delay: float = 1.0
    llm_retry_max_delay: float = 30.0

    # --- Ollama -----------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder"
    ollama_timeout: float = 180.0
    # Ollama defaults num_ctx to 2048 and silently truncates longer prompts,
    # discarding the *head*. Every prompt this agent sends exceeds that, so this
    # value is load-bearing rather than a tuning knob.
    ollama_num_ctx: int = 16384
    ollama_temperature: float = 0.0
    ollama_seed: int = 42
    # How long Ollama keeps the model — and with it the KV cache of the prompt
    # prefix — resident after a call. Ollama re-uses that cache for any prompt
    # that shares a leading run of tokens with the last one, which is exactly
    # what an append-only ReAct transcript produces, so an investigation's later
    # steps re-evaluate only their own new lines. The default of 5 minutes
    # expires between questions in a conversation; 30 covers a working session
    # without pinning the model indefinitely.
    ollama_keep_alive: str = "30m"

    # --- Pipeline tuning --------------------------------------------------
    # Onset detection
    onset_bucket_seconds: int = 60
    onset_mad_multiplier: float = 4.0
    onset_min_absolute: int = 3
    onset_lookback_multiplier: int = 4
    onset_max_lookback_hours: int = 12
    # A candidate split is only accepted if the incident side is at least this
    # many times the baseline side. Without it, ordinary variance in a service
    # that always emits a few errors per minute gets promoted to "an incident",
    # and every downstream signal is then measured against a baseline window
    # that was chosen by noise.
    onset_min_elevation: float = 2.5

    # Windows
    min_baseline_minutes: int = 10
    incident_pre_roll_seconds: int = 120

    # --- Full-window sweep ------------------------------------------------
    # Onset detection answers "where did this start"; it returns one moment, so
    # a question about six hours used to be answered about the twenty minutes
    # after the first departure and the rest of the range was never described.
    # The sweep is the other half: every distinct elevated stretch inside the
    # period asked about is found and reported, so a window holding three
    # separate incidents comes back as three issues rather than one.
    #
    # How many episodes are reported. A cap, not a target — beyond a handful the
    # list stops being a summary and becomes the histogram again.
    max_reported_episodes: int = 6
    # Consecutive quiet buckets that end an episode. Below this, one calm minute
    # inside a ten-minute failure splits it into two issues that are really one.
    episode_quiet_gap_buckets: int = 3
    # An episode has to last this long to be worth reporting on its own. A single
    # elevated bucket is noise at this resolution.
    episode_min_buckets: int = 2
    # How many services and distinct error templates are named per episode.
    episode_top_services: int = 5
    episode_top_errors: int = 3

    # --- Current status overlay -------------------------------------------
    # Root-cause and health-check answers report two things: what happened in
    # the period asked about, and what the system is doing *now*. Asked about a
    # window that ended an hour ago, an answer with no present tense leaves the
    # reader unable to tell a resolved incident from a running one. Editable
    # from the configuration page.
    recent_status_minutes: int = 30

    # Evidence budgets — these bound the LLM prompt by construction rather than
    # by truncating it afterwards.
    max_log_patterns: int = 25
    max_log_samples_per_pattern: int = 2
    max_events: int = 40
    max_metric_points: int = 20
    max_prompt_patterns: int = 12
    max_prompt_events: int = 10

    # Signal thresholds. All are ratios or baseline-relative multiples; none
    # compares a raw value against a bare constant.
    error_rate_spike_multiplier: float = 3.0
    error_rate_min_per_minute: float = 1.0
    latency_degradation_multiplier: float = 2.0
    latency_min_seconds: float = 0.25
    http_5xx_ratio_threshold: float = 0.10
    cpu_saturation_ratio: float = 0.85
    cpu_throttle_ratio: float = 0.20
    memory_pressure_ratio: float = 0.85
    traffic_surge_multiplier: float = 2.5

    # Candidate selection
    candidate_ambiguity_margin: float = 0.15

    # --- ReAct loop -------------------------------------------------------
    # Each step is a full LLM round trip, so this is the main driver of how long
    # an investigation takes. Too low and the loop cannot follow a chain across
    # services; too high and a confused model wanders instead of concluding.
    react_max_steps: int = 8

    # Transcript budget. Every step re-sends the whole investigation log, so an
    # unbounded transcript costs O(steps^2) tokens and pushes a long run into
    # the silent truncation `PromptTruncated` exists to catch. Older
    # observations are folded to a one-line digest; the most recent ones stay
    # verbatim, because those are what the next decision is made from.
    react_verbatim_steps: int = 3
    react_observation_chars: int = 1400
    react_digest_chars: int = 220

    # Confidence ceilings applied by the verifier. These are caps, not scores:
    # the answer never rises above them, whatever the model claimed.
    no_baseline_confidence_cap: float = 0.55
    # Small models report 1.0 freely. A conclusion drawn by an automated pipeline
    # from a bounded window is never certain, and a number that never expresses
    # doubt tells the reader nothing.
    max_reportable_confidence: float = 0.9

    # --- Presentation -----------------------------------------------------
    # Everything is stored and compared in UTC; this is the zone times are
    # *shown* in, on both sides of the wire. An answer that says "the departure
    # began at 05:12" beside a dashboard showing 10:42 is two facts the reader
    # has to reconcile by hand. Accepts an offset ("+05:30") or an IANA name
    # ("Asia/Colombo").
    display_timezone: str = "+05:30"
    default_investigation_hours: int = 24

    # --- Misc -------------------------------------------------------------
    prometheus_url: str = "http://localhost:9090"
    # The testbed's incident injector, used only by the evaluation harness.
    # Declared here because `extra="ignore"` means an undeclared key in .env is
    # silently dropped — INCIDENT_CONTROLLER_URL was set correctly all along and
    # went nowhere, and `settings.incident_controller_url` raised AttributeError.
    incident_controller_url: str = "http://localhost:30099"
    system_active_lookback_hours: int = 24
    log_level: str = "INFO"
    persist_investigations: bool = True

    @property
    def opensearch_auth(self) -> tuple[str, str] | None:
        if self.opensearch_username:
            return (self.opensearch_username, self.opensearch_password)
        return None


settings = Settings()
