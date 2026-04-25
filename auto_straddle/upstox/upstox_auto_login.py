"""
Headless Upstox token refresh using curl_cffi + pyotp.
Works on Raspberry Pi / Linux with no browser required.

Install:
    pip install pyotp curl_cffi

Schedule via cron at 8:30 AM daily:
    30 8 * * * cd /path/to/auto_straddle && python upstox_auto_login.py
"""

import sys
import os
import base64
import logging
import random
import string
import time
import pyotp
from curl_cffi import requests as curl_requests
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstox.credentials as credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("upstox_auto_login.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

API_KEY      = credentials.API_KEY
API_SECRET   = credentials.API_SECRET
REDIRECT_URI = credentials.REDIRECT_URI
MOBILE       = credentials.MOBILE
PIN          = credentials.PIN
TOTP_SECRET  = credentials.TOTP_SECRET
TOKEN_FILE   = "upstox_access_token.txt"

API_BASE                = "https://api.upstox.com"
SERVICE_BASE            = "https://service.upstox.com"
LOGIN_BASE              = "https://login.upstox.com"
UPSTOX_INTERNAL_REDIRECT = "https://api-v2.upstox.com/login/authorization/redirect"


def _make_session():
    uuid = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    request_id = "WPRO-" + "".join(random.choices(string.ascii_letters + string.digits, k=10))
    headers = {
        "accept": "*/*",
        "accept-language": "en-GB,en;q=0.9",
        "content-type": "application/json",
        "origin": LOGIN_BASE,
        "priority": "u=1, i",
        "referer": LOGIN_BASE,
        "sec-ch-ua": '"Chromium";v="131", "Not=A?Brand";v="24", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "x-device-details": f"platform=WEB|osName=Mac OS/10.15.7|osVersion=Chrome/131.0.0.0|appVersion=4.0.0|modelName=Chrome|manufacturer=Apple|uuid={uuid}|userAgent=Upstox 3.0 Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "x-request-id": request_id,
    }
    return curl_requests.Session(impersonate="chrome131", headers=headers), request_id


def refresh_token():
    session, request_id = _make_session()

    # Step 1 — get user_id from auth dialog redirect
    log.info("Step 1: Getting user_id from auth dialog...")
    r = session.get(
        f"{API_BASE}/v2/login/authorization/dialog",
        params={"response_type": "code", "client_id": API_KEY, "redirect_uri": REDIRECT_URI},
        allow_redirects=True,
    )
    parsed = urlparse(r.url)
    params = parse_qs(parsed.query)
    user_id   = params.get("user_id", [None])[0]
    client_id = params.get("client_id", [None])[0]
    if not user_id:
        raise RuntimeError(f"Could not get user_id from redirect. URL: {r.url}")
    log.info("user_id: %s", user_id)
    time.sleep(1)

    # Step 2 — generate OTP
    log.info("Step 2: Generating OTP...")
    r = session.post(
        f"{SERVICE_BASE}/login/open/v6/auth/1fa/otp/generate",
        json={"data": {"mobileNumber": MOBILE, "userId": user_id}},
    )
    body = r.json()
    validate_otp_token = body.get("data", {}).get("validateOTPToken")
    if not validate_otp_token:
        raise RuntimeError(f"OTP generation failed: {body}")
    log.info("OTP generated.")
    time.sleep(1)

    # Step 3 — validate TOTP (pyotp generates the 6-digit code)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    log.info("Step 3: Validating TOTP...")
    r = session.post(
        f"{SERVICE_BASE}/login/open/v4/auth/1fa/otp-totp/verify",
        json={"data": {"otp": totp, "validateOtpToken": validate_otp_token}},
    )
    if r.status_code != 200:
        raise RuntimeError(f"TOTP validation failed: {r.text}")
    log.info("TOTP validated.")
    time.sleep(1)

    # Step 4 — submit PIN (base64 encoded)
    pin_b64 = base64.b64encode(PIN.encode()).decode()
    log.info("Step 4: Submitting PIN...")
    r = session.post(
        f"{SERVICE_BASE}/login/open/v3/auth/2fa",
        params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT},
        json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_b64}},
        allow_redirects=True,
    )
    if r.status_code != 200:
        raise RuntimeError(f"PIN submission failed: {r.text}")
    log.info("PIN accepted.")
    time.sleep(1)

    # Step 5 — OAuth authorization to get auth code
    log.info("Step 5: OAuth authorization...")
    r = session.post(
        f"{SERVICE_BASE}/login/v2/oauth/authorize",
        params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT, "requestId": request_id, "response_type": "code"},
        json={"data": {"userOAuthApproval": True}},
        allow_redirects=True,
    )
    body = r.json()
    redirect_uri = body.get("data", {}).get("redirectUri", "")
    auth_code = parse_qs(urlparse(redirect_uri).query).get("code", [None])[0]
    if not auth_code:
        raise RuntimeError(f"Could not get auth code: {body}")
    log.info("Auth code obtained.")

    # Step 6 — exchange auth code for access token
    log.info("Step 6: Exchanging auth code for access token...")
    session2 = curl_requests.Session(impersonate="chrome131")
    r = session2.post(
        f"{API_BASE}/v2/login/authorization/token",
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data=f"code={auth_code}&client_id={API_KEY}&client_secret={API_SECRET}&redirect_uri={REDIRECT_URI}&grant_type=authorization_code",
    )
    res = r.json()
    if r.status_code != 200 or "access_token" not in res:
        raise RuntimeError(f"Token exchange failed: {res}")

    token = res["access_token"]
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    log.info("Token saved for user: %s (expires 3:30 AM IST)", res.get('user_name'))
    return token


if __name__ == "__main__":
    try:
        refresh_token()
        log.info("Done.")
    except Exception as e:
        log.error("Login failed: %s", e)
        sys.exit(1)
