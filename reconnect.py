import httpx, time

c = httpx.Client(verify=False, timeout=30)
BASE = "https://localhost:5000/v1/api"

print("1. Initializing brokerage session (ssodh/init)...")
r = c.post(f"{BASE}/iserver/auth/ssodh/init",
           json={"publish": True, "compete": True})
print("   ", r.status_code, r.text[:300])

for i in range(6):
    time.sleep(5)
    s = c.get(f"{BASE}/iserver/auth/status")
    print(f"2. Status check {i+1}:", s.text[:200])
    if '"authenticated":true' in s.text:
        print("\nSUCCESS — brokerage session is live!")
        a = c.get(f"{BASE}/iserver/accounts")
        print("3. Accounts:", a.text[:400])
        break
else:
    print("\nStill not authenticated — see notes below.")