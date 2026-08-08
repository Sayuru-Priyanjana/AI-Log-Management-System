import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder").strip()
    OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60").strip())

settings = Settings()
