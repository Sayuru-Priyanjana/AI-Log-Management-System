import asyncio
import httpx
import json
async def test():
    async with httpx.AsyncClient() as client:
        resp = await client.post('http://opensearch:9200/logintel-logs-*/_search', json={
            'size': 0,
            'aggs': {
                'systems': {'terms': {'field': 'system.id'}}
            }
        })
        print(json.dumps(resp.json()['aggregations']['systems']['buckets'], indent=2))
asyncio.run(test())
