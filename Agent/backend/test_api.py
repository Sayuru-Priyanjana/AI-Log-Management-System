import urllib.request
import json

url = "http://127.0.0.1:8000/api/investigations"
data = {
    "system_id": "ecommerce-platform",
    "system_name": "E-Commerce Platform",
    "environment": "production",
    "question": "Why is payment-api failing?"
}
req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')

try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code}")
    print(e.read().decode('utf-8'))
