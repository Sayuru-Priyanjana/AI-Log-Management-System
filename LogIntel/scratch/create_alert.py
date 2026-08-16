import json
import urllib.request
import time

now = int(time.time() * 1000)
alert = {
    "monitor_name": "High CPU Anomaly (Shop Demo)",
    "trigger_name": "CPU > 80%",
    "state": "ACTIVE",
    "severity": "1",
    "start_time": now - 1800000, # 30 mins ago
    "error_message": "Checkout API CPU spiked to 95% due to high traffic load."
}

req = urllib.request.Request(
    'http://localhost:9200/.opendistro-alerting-alerts/_doc',
    data=json.dumps(alert).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except Exception as e:
    print(f"Error: {e}")
