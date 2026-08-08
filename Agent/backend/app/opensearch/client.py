from opensearchpy import AsyncOpenSearch
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class OpenSearchClient:
    def __init__(self):
        auth = (settings.OPENSEARCH_USERNAME, settings.OPENSEARCH_PASSWORD)
        self.client = AsyncOpenSearch(
            hosts=[settings.OPENSEARCH_URL],
            http_auth=auth,
            use_ssl=settings.OPENSEARCH_VERIFY_SSL,
            verify_certs=settings.OPENSEARCH_VERIFY_SSL,
        )

    async def search(self, index: str, query: dict, size: int = None) -> dict:
        if size is None:
            size = settings.OPENSEARCH_MAX_RESULTS
            
        try:
            response = await self.client.search(
                body=query,
                index=index,
                size=size
            )
            return response
        except Exception as e:
            logger.error(f"OpenSearch query failed: {e}")
            raise

    async def close(self):
        await self.client.close()
