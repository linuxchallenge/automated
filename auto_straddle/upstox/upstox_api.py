"""Module providing a function for upstox"""

import traceback
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
    import pycurl
    import certifi
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
                with open('upstox_access_token.txt', 'w') as f:
                    f.write(self.access_token)
                logger.info("Successfully authenticated with Upstox")
                return
            except Exception as e:
                logger.error(f"Upstox login attempt failed: {e}")
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
        if CURL_CFFI_AVAILABLE:
            session = curl_requests.Session(impersonate="chrome131", headers=headers)
        elif PYCURL_AVAILABLE:
            session = PyCurlSession(headers=headers)
        else:
            session = requests.Session()
            session.headers.update(headers)

        # Step 1 — get user_id
        r = session.get(
            f"{API_BASE}/v2/login/authorization/dialog",
            params={"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri},
            allow_redirects=True,
        )
        params    = parse_qs(urlparse(r.url).query)
        user_id   = params.get("user_id", [None])[0]
        client_id = params.get("client_id", [None])[0]
        if not user_id:
            raise RuntimeError(f"Could not get user_id. URL: {r.url}")
        time.sleep(1)

        # Step 2 — generate OTP
        r = session.post(
            f"{SERVICE_BASE}/login/open/v6/auth/1fa/otp/generate",
            json={"data": {"mobileNumber": credentials.MOBILE, "userId": user_id}},
        )
        validate_otp_token = r.json().get("data", {}).get("validateOTPToken")
        if not validate_otp_token:
            raise RuntimeError(f"OTP generation failed: {r.json()}")
        time.sleep(1)

        # Step 3 — validate TOTP
        totp = pyotp.TOTP(credentials.TOTP_SECRET).now()
        r = session.post(
            f"{SERVICE_BASE}/login/open/v4/auth/1fa/otp-totp/verify",
            json={"data": {"otp": totp, "validateOtpToken": validate_otp_token}},
        )
        if r.status_code != 200:
            raise RuntimeError(f"TOTP validation failed: {r.text}")
        time.sleep(1)

        # Step 4 — submit PIN (base64 encoded)
        pin_b64 = base64.b64encode(credentials.PIN.encode()).decode()
        r = session.post(
            f"{SERVICE_BASE}/login/open/v3/auth/2fa",
            params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT},
            json={"data": {"twoFAMethod": "SECRET_PIN", "inputText": pin_b64}},
            allow_redirects=True,
        )
        if r.status_code != 200:
            raise RuntimeError(f"PIN submission failed: {r.text}")
        time.sleep(1)

        # Step 5 — OAuth authorization
        r = session.post(
            f"{SERVICE_BASE}/login/v2/oauth/authorize",
            params={"client_id": client_id, "redirect_uri": UPSTOX_INTERNAL_REDIRECT, "requestId": request_id, "response_type": "code"},
            json={"data": {"userOAuthApproval": True}},
            allow_redirects=True,
        )
        redirect_uri = r.json().get("data", {}).get("redirectUri", "")
        auth_code    = parse_qs(urlparse(redirect_uri).query).get("code", [None])[0]
        if not auth_code:
            raise RuntimeError(f"Could not get auth code: {r.json()}")

        # Step 6 — exchange auth code for access token
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
        res = r.json()
        if r.status_code != 200 or "access_token" not in res:
            raise RuntimeError(f"Token exchange failed: {res}")
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
        except Exception as e:
            print(f"Error executing intializeSymbolTokenMap: {e}")
            logging.error(f"Error executing intializeSymbolTokenMap: {e}")
            try:
                self.token_df = pd.read_csv('token_map_upstox.csv')
            except Exception as e1:
                print(f"Error reading local token map: {e1}")
                logging.error(f"Error reading local token map: {e1}")
                raise e1

    def getTokenInfo(self, exch_seg, instrumenttype, symbol, strike_price, pe_ce, expiry=None):
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
            return df[(df['exchange'] == 'NSE_EQ') & (df['instrument_type'] == 'EQUITY') & (df['name'] == symbol)]
            
        elif exch_seg == 'NFO' and instrumenttype in ['FUTSTK', 'FUTIDX']:
            filtered = df[(df['exchange'] == exchange) & (df['instrument_type'] == instrumenttype) & (df['name'] == symbol)]
            filtered = filtered.sort_values(by=['expiry'])
            
            today = datetime.now().date()
            if expiry is not None:
                filtered['expiry_date'] = pd.to_datetime(filtered['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return filtered[filtered['expiry_date'] == date_obj]
            else:
                if filtered.empty: return filtered
                expiry_str = filtered.iloc[0]['expiry'].strftime('%Y-%m-%d')
                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                if (expiry_date - today).days <= 10 and len(filtered) > 1:
                    return filtered.iloc[1:2]
                return filtered
                
        elif exch_seg in ['NFO', 'BFO'] and instrumenttype in ['OPTSTK', 'OPTIDX']:
            return df[(df['exchange'] == exchange) & (df['instrument_type'] == instrumenttype) & 
                      (df['name'] == symbol) & (df['strike'] == float(strike_price)) &
                      (df['option_type'] == pe_ce)].sort_values(by=['expiry'])
                      
        elif exch_seg == 'MCX' and instrumenttype == 'FUTCOM':
            filtered = df[(df['exchange'] == 'MCX_FO') & (df['instrument_type'] == 'FUTCOM') & (df['name'] == symbol)]
            filtered = filtered.sort_values(by=['expiry'])
            
            today = datetime.now().date()
            if expiry is not None:
                filtered['expiry_date'] = pd.to_datetime(filtered['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return filtered[filtered['expiry_date'] == date_obj]
            else:
                if filtered.empty: return filtered
                expiry_str = filtered.iloc[0]['expiry'].strftime('%Y-%m-%d')
                expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                if (expiry_date - today).days <= 10 and len(filtered) > 1:
                    return filtered.iloc[1:2]
                return filtered

        return pd.DataFrame()

    def _place_upstox_order(self, orderparams):
        url = self.order_url
        try:
            response = requests.post(url, headers=self.get_headers(), json=orderparams)
            res_json = response.json()
            if response.status_code == 200 and res_json.get('status') == 'success':
                return res_json['data']['order_id']
            else:
                logger.error(f"Upstox Order placement failed: {res_json}")
                print(f"Upstox Order placement failed: {res_json}")
                return None
        except requests.exceptions.Timeout:
            logger.warning("Upstox Order placement timed out")
            # Implement recent order check here if necessary
            return None
        except Exception as e:
            logger.error(f"Error placing order via target Upstox API: {e}")
            return None

    def place_order_cash(self, symbol, qty, buy_sell):
        try:
            tokenInfo = self.getTokenInfo('NSE', 'EQUITY', symbol, 0, 'X')
            if tokenInfo.empty: return -1
            
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
            logger.error(f"Order placement failed: {str(e)}")
            return -1

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, iscommodity=True):
        try:
            # Map symbol names similarly
            original_symbol = symbol
            if symbol == 'GOLD': symbol = 'GOLDM'
            elif symbol == 'SILVER': symbol = 'SILVERMIC'
            elif symbol == 'CRUDEOIL': symbol = 'CRUDEOILM'
            elif symbol == 'LEAD': symbol = 'LEADMINI'
            elif symbol == 'ZINC': symbol = 'ZINCMINI'
            elif symbol == 'ALUMINIUM': symbol = 'ALUMINI'
            
            if iscommodity:
                tokenInfo = self.getTokenInfo('MCX', 'FUTCOM', symbol, 0, 'X', expiry)
            else:
                tokenInfo = self.getTokenInfo('NFO', 'FUTIDX', symbol, 0, 'X', expiry)
            
            if tokenInfo.empty: return -1, -1
            
            t_info = tokenInfo.iloc[0]
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            total_qty = qty * lot
            
            orderparams = {
                "quantity": total_qty,
                "product": "D", # carryforward
                "validity": "DAY",
                "price": 0.0,
                "instrument_token": instrument_token,
                "order_type": "MARKET",
                "transaction_type": buy_sell,
                "disclosed_quantity": 0,
                "trigger_price": 0.0,
                "is_amo": False
            }
            
            # Retry logic could be added here
            order_id = self._place_upstox_order(orderparams)
            return (order_id, t_info['expiry']) if order_id else (-1, -1)
            
        except Exception as e:
            logger.error(f"Error executing place_order_commodity: {e}")
            return -1, -1

    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, intraday=True):
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty: return -1

            t_info = df.iloc[0]
            # Advanced expiry selection logic could be replicated here
            
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            
            if qty % lot != 0:
                logger.error(f"Upstox Quantity {qty} not multiple of lot size {lot}")
                return -1

            orderparams = {
                "quantity": qty,
                "product": "I" if intraday else "D",
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
            logger.error(f"Error executing place_order: {e}")
            return -1

    def place_order_option_buy(self, symbol, qty, buy_sell, strike_price, pe_ce):
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df.empty: return -1

            t_info = df.iloc[-1] # Simplistic replication of option select logic
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            
            if qty % lot != 0: return -1

            orderparams = {
                "quantity": qty,
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
            logger.error(f"Error executing option buy: {e}")
            return -1

    def place_order_synthetic_future(self, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):
        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce, expiry)
            if df.empty: return -1, None
            
            t_info = df.iloc[0]
            instrument_token = t_info['instrument_key']
            lot = int(t_info.get('lot_size', 1))
            
            if qty % lot != 0: return -1, None

            orderparams = {
                "quantity": qty,
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
            return (order_id, t_info['expiry']) if order_id else (-1, None)
            
        except Exception as e:
            logger.error(f"Error executing synthetic future: {e}")
            return -1, None

    def get_commodity_position(self, symbol, trade_type):
        try:
            url = f"{self.base_url}/portfolio/short-term-positions"
            response = requests.get(url, headers=self.get_headers())
            
            if response.status_code == 200:
                res_json = response.json()
                data = res_json.get('data', [])
                
                # Filter positions matching symbol and compute logic
                for pos in data:
                    tradingsymbol = pos.get('tradingsymbol', '')
                    if symbol.upper() in tradingsymbol.upper():
                        net_qty = int(pos.get('quantity', 0)) # Net quantity check vs buy/sell
                        if net_qty > 0 and trade_type == 'long':
                            return 'long', float(pos.get('average_price', 0.0))
                        elif net_qty < 0 and trade_type == 'short':
                            return 'short', float(pos.get('average_price', 0.0))
                            
            return None, 0
        except Exception as e:
            logger.error(f"Error checking Upstox commodity position: {e}")
            return None, 0

    def get_ledger_balance(self):
        try:
            url = f"{self.base_url}/user/get-funds-and-margin"
            response = requests.get(url, headers=self.get_headers())
            
            if response.status_code == 200:
                res = response.json()
                if 'data' in res and 'equity' in res['data']:
                    balance = res['data']['equity'].get('available_margin', 0)
                    return float(balance)
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching Upstox ledger balance: {e}")
            return 0.0

    def get_order_status(self, order_id):
        try:
            url = f"{self.base_url}/order/details?order_id={order_id}"
            response = requests.get(url, headers=self.get_headers())

            if response.status_code == 200:
                res = response.json()
                data = res.get('data')
                if data:
                    # /order/details returns data as an object (not a list)
                    if isinstance(data, list):
                        data = data[0] if data else None
                    if data:
                        order_status = data.get('status', '').lower()
                        average_price = data.get('average_price', 0.0)

                        if order_status == 'complete': return "Complete", average_price
                        elif order_status in ['open', 'pending']: return "Open", average_price
                        elif order_status == 'rejected': return "Rejected", average_price
                        elif order_status == 'cancelled': return "Cancelled", average_price
                        else: return "Open", average_price

            return "NotFound", -1
        except Exception as e:
            logger.error(f"Error executing get_order_status: {e}")
            return -1, -1
