"""
Raw HTTP test: bypass py5paisa library entirely.
Construct market depth request manually and POST to 5paisa API.
This isolates whether the issue is in our code/py5paisa or in the 5paisa API itself.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx
import pyotp
import fivepaisa.credentials_2 as cred_leelu
from py5paisa import FivePaisaClient

# Step 1: Login to get JWT token
print("=== Step 1: Login ===")
cred = {
    "APP_NAME": cred_leelu.APP_NAME,
    "APP_SOURCE": cred_leelu.APP_SOURCE,
    "USER_ID": cred_leelu.USER_ID,
    "PASSWORD": cred_leelu.PASSWORD,
    "USER_KEY": cred_leelu.USER_KEY,
    "ENCRYPTION_KEY": cred_leelu.ENCRYPTION_KEY,
}
client = FivePaisaClient(cred=cred)
totp_pin = pyotp.TOTP(cred_leelu.TOTP).now()
session = client.get_totp_session(cred_leelu.CLIENTCODE, totp_pin, cred_leelu.PIN)
print(f"Session token: {session[:30] if session else 'NONE'}...")
print(f"JWT token: {client.Jwt_token[:30] if client.Jwt_token else 'NONE'}...")
print(f"Client code: {client.client_code}")

# Do Login_check
client.login_check_payload['body']['RegistrationID'] = session
client.login_check_payload['head']['LoginId'] = cred_leelu.CLIENTCODE
client.login_check_payload['head']['key'] = cred_leelu.USER_KEY
client.login_check_payload['head']['appName'] = cred_leelu.APP_NAME
login_result = client.Login_check()
print(f"Login_check result: {login_result}")

# Step 2: Raw market depth request
print("\n=== Step 2: Raw Market Depth for MCX COPPER (488791) ===")

url = "https://Openapi.5paisa.com/VendorsAPI/Service1.svc/V2/MarketDepth"

headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {client.Jwt_token}',
    '5Paisa-API-Uid': 'ka7SFqAU6SC',
}

# Payload with ExchType
payload_exchtype = {
    "head": {
        "key": cred_leelu.USER_KEY,
    },
    "body": {
        "ClientCode": client.client_code,
        "Exch": "M",
        "ExchType": "D",
        "ScripCode": "488791"
    }
}

print(f"\nRequest URL: {url}")
print(f"Request Headers: {json.dumps({k: v[:40]+'...' if len(str(v))>40 else v for k,v in headers.items()}, indent=2)}")
print(f"Request Payload (ExchType): {json.dumps(payload_exchtype, indent=2)}")

resp = httpx.post(url, json=payload_exchtype, headers=headers, verify=False)
result = resp.json()
print(f"\nResponse Status Code: {resp.status_code}")
print(f"Response: {json.dumps(result, indent=2)}")

# Step 3: Try with ExchangeType instead
print("\n=== Step 3: Same request but with 'ExchangeType' instead of 'ExchType' ===")
payload_exchangetype = {
    "head": {
        "key": cred_leelu.USER_KEY,
    },
    "body": {
        "ClientCode": client.client_code,
        "Exch": "M",
        "ExchangeType": "D",
        "ScripCode": "488791"
    }
}
print(f"Request Payload (ExchangeType): {json.dumps(payload_exchangetype, indent=2)}")

resp2 = httpx.post(url, json=payload_exchangetype, headers=headers, verify=False)
result2 = resp2.json()
print(f"\nResponse: {json.dumps(result2, indent=2)}")

# Step 4: Try via py5paisa library call (for comparison)
print("\n=== Step 4: Via py5paisa library fetch_market_depth_by_scrip ===")
# Reset payload first
client.payload = {"head": {"key": cred_leelu.USER_KEY}, "body": {}}
lib_result = client.fetch_market_depth_by_scrip(Exch='M', ExchType='D', ScripCode='488791')
print(f"Library result: {json.dumps(lib_result, indent=2) if lib_result else 'None'}")

# Step 5: Try LTP for comparison
print("\n=== Step 5: LTP via fetch_market_feed_scrip ===")
client.payload = {"head": {"key": cred_leelu.USER_KEY}, "body": {}}
ltp_result = client.fetch_market_feed_scrip([{"Exch": "M", "ExchType": "D", "ScripCode": 488791}])
print(f"LTP result: {json.dumps(ltp_result, indent=2) if ltp_result else 'None'}")

# Step 6: Try deepti account for comparison
print("\n=== Step 6: Deepti account market depth for same token ===")
cred_deepti = {
    "APP_NAME": "5P58919414",
    "APP_SOURCE": "11436",
    "USER_ID": "BLe4fTGVsCt",
    "PASSWORD": "u4RzW1Pies6",
    "USER_KEY": "eJrR3CM6O0ZnArmzmLgxuToloMdLQfKa",
    "ENCRYPTION_KEY": "FLXWBhW70p0i4HhAXcH6RuB41zryq0k3",
}
client2 = FivePaisaClient(cred=cred_deepti)
totp2 = pyotp.TOTP('GU4DSMJZGQYTIXZVKBDUWRKZ').now()
session2 = client2.get_totp_session('mdeeptibhat@gmail.com', totp2, '246800')
print(f"Deepti session: {session2[:30] if session2 else 'NONE'}...")

if session2:
    client2.login_check_payload['body']['RegistrationID'] = session2
    client2.Login_check()

    # Raw request for deepti
    headers2 = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {client2.Jwt_token}',
        '5Paisa-API-Uid': 'ka7SFqAU6SC',
    }
    payload_deepti = {
        "head": {
            "key": cred_deepti["USER_KEY"],
        },
        "body": {
            "ClientCode": client2.client_code,
            "Exch": "M",
            "ExchType": "D",
            "ScripCode": "488791"
        }
    }
    resp3 = httpx.post(url, json=payload_deepti, headers=headers2, verify=False)
    result3 = resp3.json()
    print(f"Deepti Response: {json.dumps(result3, indent=2)}")
else:
    print("Deepti login failed, skipping")
