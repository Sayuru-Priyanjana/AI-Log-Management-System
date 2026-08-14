import urllib.request, json, time
import urllib.parse
now = time.time()
query = 'sum by (pod, container) (rate(container_cpu_usage_seconds_total{system_id="friend-cluster-1",namespace!="",container!=""}[2m]))'
url = f'http://localhost:9090/api/v1/query_range?query={urllib.parse.quote(query)}&start={int(now-3600)}&end={int(now)}&step=15'
req = urllib.request.Request(url)
res = urllib.request.urlopen(req)
data = json.loads(res.read())
print(f"Found {len(data['data']['result'])} series")
for s in data['data']['result']:
    print(s['metric'])
