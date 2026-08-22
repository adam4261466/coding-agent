import time, requests
start = time.time()
r = requests.post("http://localhost:11434/api/chat", json={
    "model": "gemma4:31b-cloud",
    "messages": [{"role": "user", "content": "Say hi"}],
    "stream": False,
    "options": {"num_ctx": 4096, "temperature": 0.1},
}, timeout=120)
elapsed = time.time() - start
print(f"Time: {elapsed:.1f}s")
print(f"Status: {r.status_code}")
content = r.json().get("message", {}).get("content", "")
print(f"Response: {content[:200]}")
