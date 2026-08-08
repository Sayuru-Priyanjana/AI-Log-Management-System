from abc import ABC, abstractmethod

class LLMInterface(ABC):
    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str, json_format: bool = False) -> str:
        """
        Generates a response from the underlying LLM.
        """
        pass
