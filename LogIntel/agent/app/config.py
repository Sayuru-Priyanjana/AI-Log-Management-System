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

    # --- Prometheus -------------------------------------------------------
    prometheus_url: str = "http://localhost:30090"
    prometheus_timeout: float = 30.0

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

    # --- Misc -------------------------------------------------------------
    incident_controller_url: str = "http://localhost:30099"
    log_level: str = "INFO"
    persist_investigations: bool = True

    @property
    def opensearch_auth(self) -> tuple[str, str] | None:
        if self.opensearch_username:
            return (self.opensearch_username, self.opensearch_password)
        return None


settings = Settings()
