"""
Run this script to exchange an auth code for a fresh Upstox access token.
Usage:
    python upstox_get_token.py <auth_code>
The new token is saved to upstox_access_token.txt
"""
import sys
import requests

API_KEY = "a4473751-a49b-4d63-a2b2-e811ebd1a061"
API_SECRET = "jagv8tmzit"
REDIRECT_URI = "https://127.0.0.1:5000/"

if len(sys.argv) < 2:
    print("Usage: python upstox_get_token.py <auth_code>")
    sys.exit(1)

auth_code = sys.argv[1].strip()

resp = requests.post(
    "https://api.upstox.com/v2/login/authorization/token",
    headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    data={
        "code": auth_code,
        "client_id": API_KEY,
        "client_secret": API_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    },
    timeout=30,
)

res = resp.json()
print(f"Status: {resp.status_code}")
print(f"Response: {res}")

if resp.status_code == 200 and "access_token" in res:
    token = res["access_token"]
    with open("upstox_access_token.txt", "w") as f:
        f.write(token)
    print("\nToken saved to upstox_access_token.txt")
    print(f"Token (first 40 chars): {token[:40]}...")
else:
    print("\nFailed to get token. Check the auth_code and try again.")
