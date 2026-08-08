import httpx
from .interface import LLMInterface
from app.config import settings
import json

class OllamaProvider(LLMInterface):
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL
        self.model = settings.OLLAMA_MODEL
        self.timeout = settings.OLLAMA_TIMEOUT

    async def generate(self, system_prompt: str, user_prompt: str, json_format: bool = False) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
        }
        if json_format:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
            except httpx.ConnectError:
                raise Exception("Unable to connect to Ollama. Make sure Ollama is running.")
            except httpx.TimeoutException:
                raise Exception("Ollama request timed out.")
            except httpx.HTTPStatusError as e:
                raise Exception(f"Ollama returned an HTTP error: {e.response.status_code}")
            except Exception as e:
                raise Exception(f"An unexpected error occurred while contacting Ollama: {str(e)}")
