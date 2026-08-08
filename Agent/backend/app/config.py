import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder").strip()
    OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60").strip())
    
    # OpenSearch Settings
    OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200").strip()
    OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME", "admin").strip()
    OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin").strip()
    # Pydantic/Environment booleans are usually strings initially from os.getenv
    OPENSEARCH_VERIFY_SSL = os.getenv("OPENSEARCH_VERIFY_SSL", "false").strip().lower() == "true"
    OPENSEARCH_APPLICATION_LOG_INDEX = os.getenv("OPENSEARCH_APPLICATION_LOG_INDEX", "logs-application-*").strip()
    OPENSEARCH_KUBERNETES_EVENT_INDEX = os.getenv("OPENSEARCH_KUBERNETES_EVENT_INDEX", "events-kubernetes-*").strip()
    OPENSEARCH_MAX_RESULTS = int(os.getenv("OPENSEARCH_MAX_RESULTS", "500").strip())
    
    # Prometheus Settings
    PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").strip()
    PROMETHEUS_NAMESPACE_LABEL = os.getenv("PROMETHEUS_NAMESPACE_LABEL", "namespace").strip()
    PROMETHEUS_POD_LABEL = os.getenv("PROMETHEUS_POD_LABEL", "pod").strip()
    PROMETHEUS_CONTAINER_LABEL = os.getenv("PROMETHEUS_CONTAINER_LABEL", "container").strip()
    PROMETHEUS_DEFAULT_STEP = os.getenv("PROMETHEUS_DEFAULT_STEP", "60s").strip()

settings = Settings()
