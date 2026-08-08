import httpx
import os
from dotenv import load_dotenv

load_dotenv()
base_url = os.getenv('OLLAMA_BASE_URL')
print(f"Base URL: {base_url!r}")

try:
    response = httpx.post(f"{base_url}/api/generate", json={'model': 'qwen2.5-coder', 'prompt': 'test'})
    print(response.status_code)
    print(response.text)
except Exception as e:
    print(f"Error: {e}")
