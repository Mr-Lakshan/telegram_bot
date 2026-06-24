import requests

resp = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "x-api-key": "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    },
    json={
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Say hello"}],
    },
)
print(resp.status_code, resp.json())