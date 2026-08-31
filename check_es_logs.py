import urllib.request
import json
import time

now = int(time.time() * 1000)
one_hour_ago = now - (3600 * 1000)

query = {
    "size": 0,
    "query": {
        "range": {
            "@timestamp": {
                "gte": one_hour_ago,
                "lte": now,
                "format": "epoch_millis"
            }
        }
    },
    "aggs": {
        "services": {
            "terms": {
                "field": "service.name.keyword",
                "size": 50
            }
        }
    }
}

req = urllib.request.Request(
    'http://192.168.56.10:30005/logintel-logs/_search',
    data=json.dumps(query).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)

try:
    res = urllib.request.urlopen(req, timeout=10)
    data = json.loads(res.read().decode('utf-8'))
    print("Services with logs in the last hour:")
    buckets = data.get('aggregations', {}).get('services', {}).get('buckets', [])
    for b in buckets:
        print(f"{b['key']}: {b['doc_count']} logs")
    if not buckets:
        print("No logs found in the last hour.")
except Exception as e:
    print(f"Error querying OpenSearch: {e}")
