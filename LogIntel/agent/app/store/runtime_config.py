"""
Settings that can be changed from the configuration page, without a restart.

Environment variables remain the way this is deployed — a container that needs a
human to click something before it works is not deployable. What this adds is a
*persisted override layer* on top of them, so connecting a new OpenSearch or
switching to a hosted model does not mean editing a compose file and rebuilding.

Three rules keep that honest:

**The origin of every value is reported.** A field shows whether it came from a
default, from the environment, or from an override saved here. Silently shadowing
`OPENSEARCH_URL` with something a page wrote months ago is how an afternoon
disappears.

**Only this list is editable.** Signal thresholds and window arithmetic are not
here on purpose: they are the parts whose behaviour the tests pin down, and a
text box that quietly changes what counts as an incident would make every stored
investigation incomparable with the next.

**Secrets are write-only.** An API key can be set and cleared but never read
back; the API returns whether one is present, not what it is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings, settings
from app.sources.opensearch import OpenSearchClient
from app.util.timefmt import parse_zone, set_zone

logger = logging.getLogger(__name__)

CONFIG_INDEX = "logintel-config"
DOCUMENT_ID = "runtime"


@dataclass(frozen=True)
class Field:
    name: str
    group: str
    label: str
    kind: str = "text"          # text | password | number | boolean | select
    options: tuple[str, ...] = ()
    help: str = ""
    # Whether changing it rebuilds a client. Everything here does except the
    # display zone, which only affects formatting.
    rebuilds: bool = True

    @property
    def secret(self) -> bool:
        return self.kind == "password"


FIELDS: tuple[Field, ...] = (
    # -- where the logs are ------------------------------------------------
    Field("opensearch_url", "opensearch", "OpenSearch URL",
          help="Reached from the agent. Inside a container, localhost is the container."),
    Field("opensearch_username", "opensearch", "Username",
          help="Leave empty when the security plugin is disabled."),
    Field("opensearch_password", "opensearch", "Password", kind="password"),
    Field("opensearch_verify_ssl", "opensearch", "Verify TLS certificate", kind="boolean"),
    Field("opensearch_log_index", "opensearch", "Log index pattern"),
    Field("opensearch_event_index", "opensearch", "Event index pattern"),

    # -- where the metrics are ---------------------------------------------
    Field("prometheus_url", "prometheus", "Prometheus URL",
          help="Queried directly for every investigation; not mirrored."),
    Field("incident_controller_url", "prometheus", "Incident controller URL",
          help="The testbed's fault injector. Optional — only the incidents page uses it."),

    # -- which model -------------------------------------------------------
    Field("llm_provider", "model", "Provider", kind="select",
          options=("ollama", "openai", "anthropic"),
          help="ollama runs locally and costs nothing; the others are hosted APIs."),
    Field("llm_model", "model", "Model",
          help="Leave empty on Ollama to use the Ollama model below."),
    Field("llm_api_key", "model", "API key", kind="password",
          help="Required for hosted providers. Stored in OpenSearch; never returned."),
    Field("llm_base_url", "model", "Base URL",
          help="Only for OpenAI-compatible endpoints that are not OpenAI itself."),
    Field("ollama_base_url", "model", "Ollama URL"),
    Field("ollama_model", "model", "Ollama model"),
    Field("ollama_num_ctx", "model", "Ollama context window", kind="number",
          help="Load-bearing: Ollama silently truncates prompts longer than this, "
               "keeping only the tail."),

    # -- presentation ------------------------------------------------------
    Field("display_timezone", "display", "Time zone", rebuilds=False,
          help="An offset like +05:30, or a name like Asia/Colombo. Everything is "
               "stored in UTC; this is what times are shown in."),
)

BY_NAME = {field.name: field for field in FIELDS}

# Where a value came from, so the page can say so. The *declared* default is
# read off the model rather than from a fresh Settings() — constructing one
# re-reads the environment, which makes every environment variable look like a
# built-in default and hides the distinction this exists to show.
_DEFAULTS = {name: Settings.model_fields[name].default for name in BY_NAME}
_FROM_ENV = {name: getattr(settings, name) for name in BY_NAME}


def _coerce(field: Field, value: Any) -> Any:
    if value is None:
        return None
    if field.kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if field.kind == "number":
        text = str(value).strip()
        return float(text) if "." in text else int(text)
    return str(value).strip()


def validate(name: str, value: Any) -> Any:
    """Rejects a value before it can take a dependency offline.

    A malformed URL here means every subsequent query fails with a connection
    error that looks like an outage, so the cheap checks are worth doing at the
    point of entry rather than diagnosing later.
    """
    field = BY_NAME.get(name)
    if field is None:
        raise ValueError(f"'{name}' is not an editable setting")

    coerced = _coerce(field, value)
    if coerced is None or coerced == "":
        return coerced

    if field.options and coerced not in field.options:
        raise ValueError(f"{field.label} must be one of: {', '.join(field.options)}")

    if name.endswith("_url") and not str(coerced).startswith(("http://", "https://")):
        raise ValueError(f"{field.label} must start with http:// or https://")

    if name == "display_timezone":
        parse_zone(str(coerced))        # raises ValueError with a usable message

    if name == "ollama_num_ctx" and int(coerced) < 2048:
        raise ValueError("a context window below 2048 truncates every prompt this agent sends")

    return coerced


class RuntimeConfig:
    """Loads, applies and persists the override layer."""

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client
        self.overrides: dict[str, Any] = {}
        self.persisted = True

    def rebind(self, client: OpenSearchClient) -> None:
        """Points at a new client after the settings change rebuilt them.

        Holding the old one would write the next save to the OpenSearch the user
        just moved away from — which is the one case where losing the setting
        matters most, because it is the setting that moved."""
        self._client = client

    # ---------------------------------------------------------------- apply
    def apply(self, values: dict[str, Any]) -> list[str]:
        """Writes values onto the live settings. Returns the names that changed."""
        changed = []
        for name, value in values.items():
            field = BY_NAME.get(name)
            if field is None:
                continue
            resolved = _FROM_ENV[name] if value is None else value
            if getattr(settings, name) != resolved:
                changed.append(name)
            setattr(settings, name, resolved)
            if value is None:
                self.overrides.pop(name, None)
            else:
                self.overrides[name] = value

        if "display_timezone" in values:
            set_zone(settings.display_timezone)
        return changed

    def needs_rebuild(self, changed: list[str]) -> bool:
        return any(BY_NAME[name].rebuilds for name in changed if name in BY_NAME)

    # ----------------------------------------------------------- persistence
    async def load(self) -> None:
        """Reads saved overrides and applies them. Never fatal.

        If OpenSearch is unreachable the agent still has to start: the settings
        page is the place you go to *fix* an unreachable OpenSearch, and an agent
        that refuses to boot without one cannot show it to you.
        """
        try:
            document = await self._client.get_document(CONFIG_INDEX, DOCUMENT_ID)
        except Exception as exc:            # noqa: BLE001 - degraded, not fatal
            logger.warning("Could not load saved settings (%s); using environment only", exc)
            return
        if not document:
            set_zone(settings.display_timezone)
            return

        stored = {k: v for k, v in (document.get("values") or {}).items() if k in BY_NAME}
        self.apply(stored)
        if stored:
            logger.info("Applied %d saved setting(s): %s",
                        len(stored), ", ".join(sorted(stored)))

    async def save(self) -> bool:
        """Persists the current overrides. Reports whether it stuck."""
        try:
            await self._client.index_document(
                CONFIG_INDEX, {"values": self.overrides}, doc_id=DOCUMENT_ID
            )
            self.persisted = True
        except Exception as exc:            # noqa: BLE001
            # Changing the OpenSearch URL to a broken one is exactly when this
            # fails, and exactly when the user needs to be told that the value
            # they just set will not survive a restart.
            logger.warning("Could not persist settings (%s)", exc)
            self.persisted = False
        return self.persisted

    # ---------------------------------------------------------------- report
    def describe(self) -> list[dict]:
        """Every editable field, its value in force, and where that came from."""
        out = []
        for field in FIELDS:
            current = getattr(settings, field.name)
            if field.name in self.overrides:
                source = "saved"
            elif current != _DEFAULTS[field.name]:
                source = "environment"
            else:
                source = "default"
            out.append({
                "name": field.name,
                "group": field.group,
                "label": field.label,
                "kind": field.kind,
                "options": list(field.options),
                "help": field.help,
                "source": source,
                # A secret is reported as present or absent, never echoed.
                "value": None if field.secret else current,
                "is_set": bool(current) if field.secret else None,
                "secret": field.secret,
            })
        return out
