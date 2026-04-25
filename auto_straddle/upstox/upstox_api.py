"""Module providing a function for upstox"""

import time
import logging
import base64
import random
import string
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import pandas as pd
import requests
import pyotp
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False
try:
    import pycurl  # pylint: disable=unused-import  # re-imported locally in PycurlSession
    import certifi  # pylint: disable=unused-import  # re-imported locally in PycurlSession
    PYCURL_AVAILABLE = True
except ImportError:
    pycurl = None
    PYCURL_AVAILABLE = False
import TelegramSend
import upstox.credentials as credentials

logger = logging.getLogger(__name__)


class PyCurlSession:
    """Minimal requests-like session using pycurl with cookie + header support."""

    def __init__(self, headers=None):
        import io
        self._headers = headers or {}
        self._cookies = {}
        self._io = io

    def _exec(self, method, url, params=None, json_data=None, data=None, extra_headers=None, allow_redirects=False):
        import io, json as _json, pycurl, certifi
        from urllib.parse import urlencode

        if params:
            url = url + "?" + urlencode(params)

        buf = io.BytesIO()
        c = pycurl.Curl()
        c.setopt(pycurl.URL, url)
        c.setopt(pycurl.WRITEDATA, buf)
        c.setopt(pycurl.CAINFO, certifi.where())
        c.setopt(pycurl.FOLLOWLOCATION, 1 if allow_redirects else 0)
        c.setopt(pycurl.SSL_VERIFYPEER, 1)

        # Chrome TLS ciphers
        c.setopt(pycurl.SSL_CIPHER_LIST,
            "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:"
            "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384"
        )
        c.setopt(pycurl.SSLVERSION, pycurl.SSLVERSION_TLSv1_2)

        # Merge headers
        merged = dict(self._headers)
        if extra_headers:
            merged.update(extra_headers)
        if self._cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            merged["Cookie"] = cookie_str
        header_list = [f"{k}: {v}" for k, v in merged.items()]
        c.setopt(pycurl.HTTPHEADER, header_list)

        # Capture response headers for cookies
        resp_headers = []
        c.setopt(pycurl.HEADERFUNCTION, lambda line: resp_headers.append(line.decode("utf-8", errors="ignore")))

        if method == "POST":
            if json_data is not None:
                body = _json.dumps(json_data).encode()
                c.setopt(pycurl.POST, 1)
                c.setopt(pycurl.POSTFIELDS, body.decode())
            elif data is not None:
                c.setopt(pycurl.POST, 1)
                c.setopt(pycurl.POSTFIELDS, data)
        else:
            c.setopt(pycurl.HTTPGET, 1)

        c.perform()
        status_code = c.getinfo(pycurl.RESPONSE_CODE)
        final_url   = c.getinfo(pycurl.EFFECTIVE_URL)
        c.close()

        # Parse Set-Cookie headers
        for h in resp_headers:
            if h.lower().startswith("set-cookie:"):
                cookie_part = h.split(":", 1)[1].strip().split(";")[0]
                if "=" in cookie_part:
                    k, v = cookie_part.split("=", 1)
                    self._cookies[k.strip()] = v.strip()

        raw = buf.getvalue()
        try:
            body_json = _json.loads(raw.decode())
        except Exception:
            body_json = None

        class _Resp:
            pass

        resp = _Resp()
        resp.status_code = status_code
        resp.url = final_url
        resp._body_json = body_json
        resp._raw = raw
        resp.text = raw.decode(errors="ignore")
        resp.json = lambda: body_json
        return resp

    def get(self, url, params=None, allow_redirects=False):
        return self._exec("GET", url, params=params, allow_redirects=allow_redirects)

    def post(self, url, params=None, json=None, data=None, allow_redirects=False):
        return self._exec("POST", url, params=params, json_data=json, data=data, allow_redirects=allow_redirects)


class upstox_api(object):
    # Set to True to enable verbose request/response logging for debugging.
    DEBUG = False

    # MCX commodity display name -> actual mini/micro contract prefix.
    # Used by both place_order_commodity (to look up the contract) and
    # get_commodity_position (to match open positions in the portfolio).
    SYMBOL_PREFIX_MAP = {
        'GOLD': 'GOLDM',
        'SILVER': 'SILVERMIC',
        'COPPER': 'COPPER',
        'CRUDEOIL': 'CRUDEOILM',
        'NATURALGAS': 'NATGASMINI',
        'LEAD': 'LEADMINI',
        'ZINC': 'ZINCMINI',
        'ALUMINIUM': 'ALUMINI',
    }

    def __init__(self):
        self.client_id = credentials.API_KEY
        self.client_secret = credentials.API_SECRET
        self.redirect_uri = credentials.REDIRECT_URI
        self.access_token = None

        self.base_url = "https://api.upstox.com/v2"
        self.order_url = "https://api-hft.upstox.com/v2/order/place"

        self._authenticate()
        self.intializeSymbolTokenMap()

    def _authenticate(self):
        attempts = 3
        while attempts > 0:
            attempts -= 1
            try:
                self.access_token = self._headless_login()
                with open('upstox_access_token.txt', 'w', encoding='utf-8') as f:
                    f.write(self.access_token)
                logger.info("Successfully authenticated with Upstox")
                return
            except Exception as e:
                logger.error("Upstox login attempt failed: %s", e)
                if attempts > 0:
                    time.sleep(2)
        logger.error("All Upstox login attempts failed")

    def _headless_login(self):
        API_BASE                 = "https://api.upstox.com"
        SERVICE_BASE             = "https://service.upstox.com"
        LOGIN_BASE               = "https://login.upstox.com"
        UPSTOX_INTERNAL_REDIRECT = "https://api-v2.upstox.com/login/authorization/redirect"

        uuid       = "".join(random.choices(string.ascii_letters + string.digits, k=16))
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
        session_type = 'curl_cffi' if CURL_CFFI_AVAILABLE else ('pycurl' if PYCURL_AVAILABLE else 'requests')
        logger.debug("_headless_login: starting with session=%s", session_type)
        if CURL_CFFI_AVAILABLE:
            session = curl_requests.Session(impersonate="chrome131", headers=headers)
        elif PYCURL_AVAILABLE:
            session = PyCurlSession(headers=headers)
        else:
            session = requests.Session()
            session.headers.update(headers)

        # Step 1 — get user_id
        logger.debug("_headless_login step 1: requesting auth dialog")
        r = session.get(
            f"{API_BASE}/v2/login/authorization/dialog",
            params={"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri},
            allow_redirects=True,
        )
        logger.debug("_headless_login step 1: status=%s, final_url=%s", r.status_code, r.url)
        params    = parse_qs(urlparse(r.url).query)
        user_id   = params.get("user_id", [None])[0]
        client_id = params.get("client_id", [None])[0]
        if not user_id:
            raise RuntimeError(f"Could not get user_id. URL: {r.url}")
        logger.debug("_headless_login step 1: got user_id=%s", user_id)
        time.sleep(1)

        # Step 2 — generate OTP
        logger.debug("_headless_login step 2: generating OTP")
        r = session.post(
            f"{SERVICE_BASE}/login/open/v6/auth/1fa/otp/generate",
            json={"data": {"mobileNumber": credentials.MOBILE, "userId": user_id}},
        )
        logger.debug("_headless_login step 2: status=%s", r.status_code)
        validate_otp_token = r.json().get("data", {}).get("validateOTPToken")
        if not validate_otp_token:
            raise RuntimeError(f"OTP generation failed: {r.json()}")
        time.sleep(1)

        # Step 3 — validate TOTP
        logger.debug("_headless_login step 3: validating TOTP")
        totp = pyotp.TOTP(credentials.TOTP_SECRET).now()
        r = session.post(
            f"{SERVICE_BASE}/login/open/v4/auth/1fa/otp-totp/verify",
            json={"data": {"otp": totp, "validateOtpToken": validate_otp_token}},
        )
        logger.debug("_headless_login step 3: status=%s", r.status_code)
        if r.status_code != 200:
            raise RuntimeError(f"TOTP validation failed: {r.text}")
        time.sleep(1)

        # Step 4 — submit PIN (base64 encoded)
        logger.debug("_headless_login step 4: submitting PIN")
        pin_b64 = base64.b64encode(credentials.PIN.encode()).decode()
        r = session.post(
            f"{SERVICE_BASE}/login/open/v3/auth/2fa",
            params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT},
            json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_b64}},
            allow_redirects=True,
        )
        logger.debug("_headless_login step 4: status=%s", r.status_code)
        if r.status_code != 200:
            raise RuntimeError(f"PIN submission failed: {r.text}")
        time.sleep(1)

        # Step 5 — OAuth authorization
        logger.debug("_headless_login step 5: OAuth authorize")
        r = session.post(
            f"{SERVICE_BASE}/login/v2/oauth/authorize",
            params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT, "requestId": request_id, "response_type": "code"},
            json={"data": {"userOAuthApproval": True}},
            allow_redirects=True,
        )
        logger.debug("_headless_login step 5: status=%s", r.status_code)
        redirect_uri = r.json().get("data", {}).get("redirectUri", "")
        auth_code    = parse_qs(urlparse(redirect_uri).query).get("code", [None])[0]
        if not auth_code:
            raise RuntimeError(f"Could not get auth code: {r.json()}")
        logger.debug("_headless_login step 5: auth code obtained")

        # Step 6 — exchange auth code for access token
        logger.debug("_headless_login step 6: exchanging auth code for token")
        if CURL_CFFI_AVAILABLE:
            session2 = curl_requests.Session(impersonate="chrome131")
        elif PYCURL_AVAILABLE:
            session2 = PyCurlSession()
        else:
            session2 = requests.Session()
        r = session2.post(
            f"{API_BASE}/v2/login/authorization/token",
            headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
            data=f"code={auth_code}&client_id={self.client_id}&client_secret={self.client_secret}&redirect_uri={self.redirect_uri}&grant_type=authorization_code",
        )
        logger.debug("_headless_login step 6: status=%s", r.status_code)
        res = r.json()
        if r.status_code != 200 or "access_token" not in res:
            raise RuntimeError(f"Token exchange failed: {res}")
        logger.debug("_headless_login: token obtained successfully")
        return res["access_token"]

    def get_headers(self):
        return {
            'accept': 'application/json',
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

    def intializeSymbolTokenMap(self):
        try:
            url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
            self.token_df = pd.read_csv(url)

            # Additional cleanup for Upstox instruments if needed
            self.token_df['expiry'] = pd.to_datetime(self.token_df['expiry'])
            if 'strike' in self.token_df.columns:
                self.token_df['strike'] = pd.to_numeric(self.token_df['strike'], errors='coerce')

            self.token_df.to_csv('token_map_upstox.csv', index=False)
            logger.debug("intializeSymbolTokenMap: loaded %s rows from network", len(self.token_df))
        except Exception as e:
            print(f"Error executing intializeSymbolTokenMap: {e}")
            logging.error("Error executing intializeSymbolTokenMap: %s", e)
            try:
                self.token_df = pd.read_csv('token_map_upstox.csv')
                logger.debug("intializeSymbolTokenMap: loaded %s rows from local cache", len(self.token_df))
            except Exception as e1:
                print(f"Error reading local token map: {e1}")
                logging.error("Error reading local token map: %s", e1)
                raise e1

    def getTokenInfo(self, exch_seg, instrumenttype, symbol, strike_price, pe_ce, expiry=None):
        logger.debug("getTokenInfo: exch=%s type=%s symbol=%s strike=%s pe_ce=%s expiry=%s", exch_seg, instrumenttype, symbol, strike_price, pe_ce, expiry)
        df = self.token_df

        # Map exchange segment names to Upstox CSV exchange values
        exch_map = {
            'NSE': 'NSE_EQ',
            'NFO': 'NSE_FO',
            'MCX': 'MCX_FO',
            'BSE': 'BSE_EQ',
            'BFO': 'BSE_FO',
        }
        exchange = exch_map.get(exch_seg, exch_seg)

        if symbol == "SENSEX":
            exchange = "BSE_FO"

        # Note: Upstox option_type is 'PE' or 'CE'
        # Upstox strike is normally actual float without the *100 used by AngelOne, but check data if needed.

        # We approximate the standard filtering:
        if exch_seg == 'NSE':
            result = df[(df['exchange'] == 'NSE_EQ') & (df['instrument_type'] == 'EQUITY') & (df['name'] == symbol)]
            logger.debug("getTokenInfo NSE_EQ result: %s row(s)", len(result))
            return result

        if exch_seg == 'NFO' and instrumenttype in ['FUTSTK', 'FUTIDX']:
            filtered = df[(df['exchange'] == exchange) & (df['instrument_type'] == instrumenttype) & (df['name'] == symbol)]
            filtered = filtered.sort_values(by=['expiry'])

            today = datetime.now().date()
            if expiry is not None:
                filtered['expiry_date'] = pd.to_datetime(filtered['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return filtered[filtered['expiry_date'] == date_obj]
            if filtered.empty:
                return filtered
            expiry_str = filtered.iloc[0]['expiry'].strftime('%Y-%m-%d')
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            if (expiry_date - today).days <= 10 and len(filtered) > 1:
                return filtered.iloc[1:2]
            return filtered

        if exch_seg in ['NFO', 'BFO'] and instrumenttype in ['OPTSTK', 'OPTIDX']:
            result = df[(df['exchange'] == exchange) & (df['instrument_type'] == instrumenttype) &
                        (df['name'] == symbol) & (df['strike'] == float(strike_price)) &
                        (df['option_type'] == pe_ce)].sort_values(by=['expiry'])
            logger.debug("getTokenInfo OPTIDX result: %s row(s) for %s %s %s", len(result), symbol, strike_price, pe_ce)
            return result

        if exch_seg == 'MCX' and instrumenttype == 'FUTCOM':
            filtered = df[(df['exchange'] == 'MCX_FO') & (df['instrument_type'] == 'FUTCOM') &
                          (df['tradingsymbol'].str.upper().str.startswith(symbol.upper()))]
            filtered = filtered.sort_values(by=['expiry'])

            today = datetime.now().date()
            if expiry is not None:
                filtered['expiry_date'] = pd.to_datetime(filtered['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return filtered[filtered['expiry_date'] == date_obj]
            if filtered.empty:
                return filtered
            expiry_str = filtered.iloc[0]['expiry'].strftime('%Y-%m-%d')
            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            if (expiry_date - today).days <= 10 and len(filtered) > 1:
                return filtered.iloc[1:2]
            return filtered

        return pd.DataFrame()

    def get_best_price(self, instrument_token, buy_sell):
        """Fetch best price for limit orders using Upstox market depth, falling back to LTP.

        For BUY returns best ask; for SELL returns best bid.
        Falls back to LTP ± 0.5% buffer if depth is unavailable.

        Args:
            instrument_token: Upstox instrument key (e.g. 'MCX_FO|466029')
            buy_sell: 'BUY' or 'SELL'

        Returns:
            float: price to use in limit order, or 0 if all methods fail
        """
        # Attempt 1: market depth via /market-quote/full
        try:
            url = f"{self.base_url}/market-quote/full"
            params = {"instrument_key": instrument_token}
            response = requests.get(url, headers=self.get_headers(), params=params, timeout=10)
            if response.status_code == 200:
                data = response.json().get('data', {})
                quote = next(iter(data.values()), {}) if data else {}
                depth = quote.get('depth', {})
                if buy_sell == 'BUY':
                    asks = depth.get('sell', [])
                    for ask in asks:
                        p = float(ask.get('price', 0))
                        if p > 0:
                            logger.info("Upstox market depth ask price for %s: %s", instrument_token, p)
                            return p
                else:
                    bids = depth.get('buy', [])
                    for bid in bids:
                        p = float(bid.get('price', 0))
                        if p > 0:
                            logger.info("Upstox market depth bid price for %s: %s", instrument_token, p)
                            return p
            logger.warning("Upstox market depth returned no usable price for %s, falling back to LTP", instrument_token)
        except Exception as e:
            logger.warning("Upstox market depth failed for %s: %s, falling back to LTP", instrument_token, e)

        # Fallback: LTP via /market-quote/ltp
        try:
            url = f"{self.base_url}/market-quote/ltp"
            params = {"instrument_key": instrument_token}
            response = requests.get(url, headers=self.get_headers(), params=params, timeout=10)
            if response.status_code == 200:
                data = response.json().get('data', {})
                quote = next(iter(data.values()), {}) if data else {}
                ltp = float(quote.get('last_price', 0))
                if ltp > 0:
                    if buy_sell == 'BUY':
                        price = round(ltp * 1.005, 2)
                    else:
                        price = round(ltp * 0.995, 2)
                    logger.info("Upstox LTP fallback price for %s: %s (ltp=%s)", instrument_token, price, ltp)
                    return price
            logger.error("Upstox LTP also returned no price for %s", instrument_token)
        except Exception as e:
            logger.error("Upstox LTP fallback failed for %s: %s", instrument_token, e)

        return 0

    def _place_upstox_order(self, orderparams):
        url = self.order_url
        if self.DEBUG:
            logger.debug("_place_upstox_order payload: %s", orderparams)
        response = requests.post(url, headers=self.get_headers(), json=orderparams, timeout=30)
        res_json = response.json()
        logger.debug("_place_upstox_order response: status=%s", response.status_code)
        if self.DEBUG:
            logger.debug("_place_upstox_order response body: %s", res_json)
        if response.status_code == 200 and res_json.get('status') == 'success':
            order_id = res_json['data']['order_id']
            logger.debug("_place_upstox_order: order_id=%s", order_id)
            return order_id
        logger.error("Upstox Order placement failed: %s", res_json)
        print(f"Upstox Order placement failed: {res_json}")
        return None

    def _find_recent_order(self, instrument_token, transaction_type, qty, product=None):
        """Check order book for a non-rejected order matching the given params.

        Used after a timeout to detect if the server processed the order before
        the client gave up, to avoid placing a duplicate on retry.

        Returns the order_id string if found, None otherwise.
        """
        logger.debug("_find_recent_order: token=%s side=%s qty=%s product=%s", instrument_token, transaction_type, qty, product)
        try:
            url = f"{self.base_url}/order/retrieve-all"
            response = requests.get(url, headers=self.get_headers(), timeout=15)
            if response.status_code != 200:
                logger.debug("_find_recent_order: order book fetch failed status=%s", response.status_code)
                return None
            data = response.json().get('data') or []
            logger.debug("_find_recent_order: scanning %s orders", len(data))
            for o in data:
                status = str(o.get('status', '')).lower()
                if status in ('rejected', 'cancelled'):
                    continue
                if (str(o.get('instrument_token', '')) == str(instrument_token)
                        and str(o.get('transaction_type', '')) == str(transaction_type)
                        and int(o.get('quantity', 0)) == int(qty)):
                    if product is not None and str(o.get('product', '')) != str(product):
                        continue
                    found_id = o.get('order_id')
                    logger.debug("_find_recent_order: matched order_id=%s status=%s", found_id, o.get('status'))
                    return found_id
        except Exception as e:
            logger.error("Error checking Upstox order book for recent order: %s", e)
        logger.debug("_find_recent_order: no matching order found")
        return None

    def _validate_order_id(self, order_id):
        """Return True if order_id is a non-empty, non-zero value."""
        return order_id is not None and order_id != '' and order_id != 0

    def place_order_cash(self, symbol, qty, buy_sell):
        try:
            tokenInfo = self.getTokenInfo('NSE', 'EQUITY', symbol, 0, 'X')
            if tokenInfo.empty:
                return -1

            instrument_token = tokenInfo.iloc[0]['instrument_key']

            orderparams = {
                "quantity": int(qty),
                "product": "D",
                "validity": "DAY",
                "price": 0.0,
                "instrument_token": instrument_token,
                "order_type": "MARKET",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }

            order_id = self._place_upstox_order(orderparams)
            return order_id if order_id else -1
        except Exception as e:
            logger.error("Order placement failed: %s", str(e))
            return -1

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, iscommodity=True):  # pylint: disable=too-many-branches
        original_symbol = symbol  # preserve before remapping for position check
        logger.debug("place_order_commodity: %s qty=%s side=%s expiry=%s iscommodity=%s", symbol, qty, buy_sell, expiry, iscommodity)
        try:
            # Map display name to actual mini/micro contract symbol
            mapped = self.SYMBOL_PREFIX_MAP.get(symbol.upper(), symbol)
            logger.debug("place_order_commodity: symbol mapped %s -> %s", symbol, mapped)
            symbol = mapped

            if iscommodity:
                tokenInfo = self.getTokenInfo('MCX', 'FUTCOM', symbol, 0, 'X', expiry)
            else:
                tokenInfo = self.getTokenInfo('NFO', 'FUTIDX', symbol, 0, 'X', expiry)

            if tokenInfo.empty:
                logger.error("place_order_commodity: no token found for %s expiry=%s", symbol, expiry)
                return -1, -1

            t_info = tokenInfo.iloc[0]
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            total_qty = qty * lot
            logger.debug("place_order_commodity: token=%s lot=%s total_qty=%s expiry=%s", instrument_token, lot, total_qty, t_info['expiry'])
            product = "D"  # carryforward

            price = self.get_best_price(instrument_token, buy_sell)
            if price <= 0:
                logger.error("place_order_commodity: could not get price for %s, aborting", instrument_token)
                return -1, -1

            orderparams = {
                "quantity": total_qty,
                "product": product,
                "validity": "DAY",
                "price": price,
                "instrument_token": instrument_token,
                "order_type": "LIMIT",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Token: {instrument_token}, Lot: {lot}, Price: {price}")
            order_id = None
            try:
                try:
                    order_id = self._place_upstox_order(orderparams)
                except requests.exceptions.Timeout:
                    logger.warning("Upstox place_order_commodity timed out, checking order book before retry")
                    time.sleep(5)
                    existing = self._find_recent_order(instrument_token, buy_sell, total_qty, product)
                    if existing:
                        logger.info("Order %s already exists after timeout, skipping retry", existing)
                        order_id = existing
                    else:
                        try:
                            order_id = self._place_upstox_order(orderparams)
                        except Exception as e2:
                            logger.error("Error executing place_order_commodity after timeout: %s", e2)
                            return -1, -1
            except Exception as e:
                try:
                    logger.error("Error placing commodity order, trying again: %s", e)
                    try:
                        TelegramSend.telegram_send_api().send_message(
                            "-4008545231", f"Warning upstox {symbol} order Pls check")
                    except Exception as telegram_error:
                        logger.debug("Failed to send Telegram alert: %s", telegram_error)
                    time.sleep(2)

                    # Before retrying, check if the position already exists at the broker.
                    # The order may have been processed despite the exception, and retrying
                    # would create a duplicate position.
                    trade_type = 'long' if buy_sell == 'BUY' else 'short'
                    pos_type, _ = self.get_commodity_position(original_symbol, trade_type)
                    if pos_type is not None:
                        logger.info("Position already exists for %s (%s) after exception. Skipping retry to avoid duplicate.", original_symbol, trade_type)
                        return -1, t_info['expiry']

                    order_id = self._place_upstox_order(orderparams)
                except Exception as e1:
                    logger.error("Error executing place_order_commodity: %s", e1)
                    return -1, -1

            if not self._validate_order_id(order_id):
                logger.error("Upstox API returned invalid order ID: %s for commodity %s", order_id, symbol)
                return -1, -1

            return order_id, t_info['expiry']

        except Exception as e:
            logger.error("Error executing place_order_commodity: %s", e)
            return -1, -1

    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, intraday=True):  # pylint: disable=too-many-branches
        logger.debug("place_order: %s %s%s qty=%s side=%s intraday=%s", symbol, strike_price, pe_ce, qty, buy_sell, intraday)
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty:
                logger.error("place_order: no token found for %s %s%s", symbol, strike_price, pe_ce)
                return -1

            # Skip past-expiry contracts
            try:
                if pd.to_datetime(df.iloc[0]['expiry']).date() < datetime.now().date():
                    df = df.iloc[1:] if len(df) > 1 else df
            except Exception:
                pass

            if df.empty:
                return -1
            t_info = df.iloc[0]
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            logger.debug("place_order: token=%s lot=%s expiry=%s", instrument_token, lot, t_info['expiry'])

            if qty % lot != 0:
                logger.error("Upstox Quantity %s not multiple of lot size %s", qty, lot)
                return -1

            product = "I" if intraday else "D"
            orderparams = {
                "quantity": qty,
                "product": product,
                "validity": "DAY",
                "price": 0.0,
                "instrument_token": instrument_token,
                "order_type": "MARKET",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Token: {instrument_token}, Lot: {lot}")
            order_id = None
            try:
                try:
                    order_id = self._place_upstox_order(orderparams)
                except requests.exceptions.Timeout:
                    logger.warning("Upstox place_order timed out, checking order book before retry")
                    time.sleep(5)
                    existing = self._find_recent_order(instrument_token, buy_sell, qty, product)
                    if existing:
                        logger.info("Order %s already exists after timeout, skipping retry", existing)
                        order_id = existing
                    else:
                        try:
                            order_id = self._place_upstox_order(orderparams)
                        except Exception as e2:
                            logger.error("Error executing place_order after timeout: %s", e2)
                            return -1
            except Exception as e:
                try:
                    logger.error("Error executing place_order again: %s", e)
                    try:
                        TelegramSend.telegram_send_api().send_message(
                            "-4008545231", f"Warning upstox {symbol} order Pls check")
                    except Exception as telegram_error:
                        logger.debug("Failed to send Telegram alert: %s", telegram_error)
                    time.sleep(2)
                    order_id = self._place_upstox_order(orderparams)
                except Exception as e1:
                    logger.error("Error executing place_order: %s", e1)
                    return -1

            if not self._validate_order_id(order_id):
                logger.error("Upstox API returned invalid order ID: %s for %s", order_id, symbol)
                return -1

            return order_id

        except Exception as e:
            logger.error("Error executing place_order: %s", e)
            return -1

    def place_order_option_buy(self, symbol, qty, buy_sell, strike_price, pe_ce):  # pylint: disable=too-many-branches
        logger.debug("place_order_option_buy: %s %s%s qty=%s side=%s", symbol, strike_price, pe_ce, qty, buy_sell)
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty:
                logger.error("place_order_option_buy: no token found for %s %s%s", symbol, strike_price, pe_ce)
                return -1

            # Pick current month's last expiry (same logic as AngelOne)
            try:
                today = datetime.now().date()
                next_month = today.replace(day=28) + timedelta(days=4)
                last_day_of_month = next_month - timedelta(days=next_month.day)
                df = df.copy()
                df['expiry_date'] = pd.to_datetime(df['expiry']).dt.date
                current_month = df[df['expiry_date'] <= last_day_of_month]
                if not current_month.empty:
                    t_info = current_month.iloc[-1]
                else:
                    t_info = df[df['expiry_date'] > last_day_of_month].iloc[-1] if not df[df['expiry_date'] > last_day_of_month].empty else df.iloc[-1]
            except Exception:
                t_info = df.iloc[-1]

            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            logger.debug("place_order_option_buy: token=%s lot=%s expiry=%s", instrument_token, lot, t_info['expiry'])

            if qty % lot != 0:
                logger.error("Upstox Quantity %s not multiple of lot size %s", qty, lot)
                return -1

            product = "D"
            orderparams = {
                "quantity": qty,
                "product": product,
                "validity": "DAY",
                "price": 0.0,
                "instrument_token": instrument_token,
                "order_type": "MARKET",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Token: {instrument_token}, Lot: {lot}")
            order_id = None
            try:
                try:
                    order_id = self._place_upstox_order(orderparams)
                except requests.exceptions.Timeout:
                    logger.warning("Upstox place_order_option_buy timed out, checking order book before retry")
                    time.sleep(5)
                    existing = self._find_recent_order(instrument_token, buy_sell, qty, product)
                    if existing:
                        logger.info("Order %s already exists after timeout, skipping retry", existing)
                        order_id = existing
                    else:
                        try:
                            order_id = self._place_upstox_order(orderparams)
                        except Exception as e2:
                            logger.error("Error executing place_order_option_buy after timeout: %s", e2)
                            return -1
            except Exception as e:
                try:
                    logger.error("Error placing option buy order, trying again: %s", e)
                    try:
                        TelegramSend.telegram_send_api().send_message(
                            "-4008545231", f"Warning upstox {symbol} option buy order Pls check")
                    except Exception as telegram_error:
                        logger.debug("Failed to send Telegram alert: %s", telegram_error)
                    time.sleep(2)
                    order_id = self._place_upstox_order(orderparams)
                except Exception as e1:
                    logger.error("Error executing place_order_option_buy: %s", e1)
                    return -1

            if not self._validate_order_id(order_id):
                logger.error("Upstox API returned invalid order ID: %s for option buy %s", order_id, symbol)
                return -1

            return order_id

        except Exception as e:
            logger.error("Error executing option buy: %s", e)
            return -1

    def place_order_synthetic_future(self, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):  # pylint: disable=too-many-branches
        logger.debug("place_order_synthetic_future: %s %s%s qty=%s side=%s expiry=%s", symbol, strike_price, pe_ce, qty, buy_sell, expiry)
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce, expiry)
            if df.empty:
                logger.error("place_order_synthetic_future: no token found for %s %s%s", symbol, strike_price, pe_ce)
                return -1, None

            # Pick expiry at least 8 days out (same logic as AngelOne)
            try:
                today = datetime.now().date()
                df = df.copy()
                df['expiry_date'] = pd.to_datetime(df['expiry']).dt.date
                if expiry is None:
                    future = df[df['expiry_date'] > (today + timedelta(days=8))]
                    if future.empty:
                        logger.error("place_order_synthetic_future: no valid future expiries found for %s", symbol)
                        return -1, None
                    df = future
                else:
                    try:
                        target_expiry = pd.to_datetime(expiry).date()
                        matching = df[df['expiry_date'] == target_expiry]
                        if matching.empty:
                            logger.error("place_order_synthetic_future: no contract found for specified expiry %s", target_expiry)
                            return -1, None
                        df = matching
                    except Exception as e:
                        logger.error("place_order_synthetic_future: error parsing expiry %s: %s", expiry, e)
                        return -1, None
            except Exception:
                pass

            if df.empty:
                return -1, None
            t_info = df.iloc[0]
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            logger.info("Upstox: Selected contract: %s, lot=%s, expiry=%s", instrument_token, lot, t_info['expiry'])
            logger.debug("place_order_synthetic_future: token=%s lot=%s expiry=%s", instrument_token, lot, t_info['expiry'])

            if qty % lot != 0:
                logger.error("Quantity %s not multiple of lot size %s", qty, lot)
                return -1, None

            product = "D"
            orderparams = {
                "quantity": qty,
                "product": product,
                "validity": "DAY",
                "price": 0.0,
                "instrument_token": instrument_token,
                "order_type": "MARKET",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }

            # Try twice with retry + telegram alert on first failure
            for attempt in range(2):
                try:
                    order_id = self._place_upstox_order(orderparams)
                    if not self._validate_order_id(order_id):
                        logger.error("Upstox API returned invalid order ID: %s for synthetic future %s", order_id, symbol)
                        continue
                    logger.info("Order placed successfully: %s", order_id)
                    return order_id, t_info['expiry']
                except requests.exceptions.Timeout:
                    logger.warning("Order placement timeout (attempt %s)", attempt+1)
                    time.sleep(2)
                    # On timeout, check order book to avoid duplicate
                    existing = self._find_recent_order(instrument_token, buy_sell, qty, product)
                    if existing:
                        logger.info("Order %s already exists after timeout, skipping retry", existing)
                        return existing, t_info['expiry']
                except Exception as e:
                    logger.error("Order placement error (attempt %s): %s", attempt+1, e)
                    if attempt == 0:
                        time.sleep(2)
                        try:
                            TelegramSend.telegram_send_api().send_message(
                                "-4008545231",
                                f"Warning upstox {symbol} synthetic order failed: {str(e)[:100]}")
                        except Exception as telegram_error:
                            logger.debug("Failed to send Telegram alert: %s", telegram_error)

            return -1, None

        except Exception as e:
            logger.error("Error executing synthetic future: %s", e)
            return -1, None

    def get_commodity_position(self, symbol, trade_type):
        logger.debug("get_commodity_position: symbol=%s trade_type=%s", symbol, trade_type)
        try:
            mcx_prefix = self.SYMBOL_PREFIX_MAP.get(symbol.upper(), symbol.upper())
            logger.debug("get_commodity_position: looking for prefix=%s", mcx_prefix)

            # MCX carryforward positions are under long-term-positions
            url = f"{self.base_url}/portfolio/long-term-positions"
            response = requests.get(url, headers=self.get_headers(), timeout=10)

            if response.status_code == 200:
                data = response.json().get('data', [])
                logger.debug("get_commodity_position: %s long-term position(s) returned", len(data))
                if self.DEBUG:
                    for pos in data:
                        logger.debug("  position: %s qty=%s", pos.get('tradingsymbol'), pos.get('quantity'))
                for pos in data:
                    tradingsymbol = pos.get('tradingsymbol', '').upper()
                    if tradingsymbol.startswith(mcx_prefix.upper()):
                        net_qty = int(pos.get('quantity', 0))
                        if net_qty > 0 and trade_type == 'long':
                            avg = float(pos.get('average_price', 0.0))
                            logger.info("Upstox: Found LONG position for %s: qty=%s avg=%s", symbol, net_qty, avg)
                            return 'long', avg
                        if net_qty < 0 and trade_type == 'short':
                            avg = float(pos.get('average_price', 0.0))
                            logger.info("Upstox: Found SHORT position for %s: qty=%s avg=%s", symbol, net_qty, avg)
                            return 'short', avg

            logger.info("Upstox: No matching %s position found for %s", trade_type, symbol)
            return None, 0
        except Exception as e:
            logger.error("Error checking Upstox commodity position: %s", e)
            return None, 0

    def get_ledger_balance(self):
        logger.debug("get_ledger_balance: fetching funds and margin")
        try:
            url = f"{self.base_url}/user/get-funds-and-margin"
            response = requests.get(url, headers=self.get_headers(), timeout=10)
            logger.debug("get_ledger_balance: response status=%s", response.status_code)

            if response.status_code == 200:
                res = response.json()
                if self.DEBUG:
                    logger.debug("get_ledger_balance raw: %s", res)
                data = res.get('data', {})
                equity    = float((data.get('equity')    or {}).get('available_margin', 0) or 0)
                commodity = float((data.get('commodity') or {}).get('available_margin', 0) or 0)
                balance = equity + commodity
                logger.info("Upstox ledger balance: equity=%s commodity=%s total=%s", equity, commodity, balance)
                return balance
            return 0.0
        except Exception as e:
            logger.error("Error fetching Upstox ledger balance: %s", e)
            return 0.0

    def get_order_status(self, order_id):
        logger.debug("get_order_status: order_id=%s", order_id)
        try:
            # Defensive check for NaN, None or non-numeric values
            if pd.isna(order_id) or order_id is None or order_id == -1 or order_id == '' or str(order_id).lower() == 'nan':
                logger.error("Invalid order_id received for status check: %s", order_id)
                return "NotFound", -1

            url = f"{self.base_url}/order/details?order_id={order_id}"
            response = requests.get(url, headers=self.get_headers(), timeout=10)
            logger.debug("get_order_status: response status=%s", response.status_code)

            if response.status_code == 200:
                res = response.json()
                if self.DEBUG:
                    logger.debug("get_order_status raw: %s", res)
                data = res.get('data')
                if data:
                    # /order/details returns data as an object (not a list)
                    if isinstance(data, list):
                        data = data[0] if data else None
                    if data:
                        order_status = data.get('status', '').lower()
                        average_price = data.get('average_price', 0.0)
                        logger.debug("get_order_status: order_id=%s status=%s avg_price=%s", order_id, order_status, average_price)

                        if order_status == 'complete':
                            return "Complete", average_price
                        if order_status in ['open', 'pending']:
                            return "Open", average_price
                        if order_status == 'rejected':
                            return "Rejected", average_price
                        if order_status == 'cancelled':
                            return "Cancelled", average_price
                        return "Open", average_price

            return "NotFound", -1
        except Exception as e:
            logger.error("Error executing get_order_status: %s", e)
            return "NotFound", -1
