import datetime
import enum
import json
import logging
import random
import re
import string
import time
import pandas as pd
from websocket import create_connection
import requests
from urllib.parse import quote
from pathlib import Path

logger = logging.getLogger(__name__)


class Interval(enum.Enum):
    in_1_minute = "1"
    in_3_minute = "3"
    in_5_minute = "5"
    in_15_minute = "15"
    in_30_minute = "30"
    in_45_minute = "45"
    in_1_hour = "1H"
    in_2_hour = "2H"
    in_3_hour = "3H"
    in_4_hour = "4H"
    in_daily = "1D"
    in_weekly = "1W"
    in_monthly = "1M"


class TvDatafeed:
    __sign_in_url = 'https://www.tradingview.com/accounts/signin/'
    __search_url = 'https://symbol-search.tradingview.com/symbol_search/?text={}&hl=1&exchange={}&lang=en&type=&domain=production'
    __ws_headers = json.dumps({"Origin": "https://data.tradingview.com"})
    __signin_headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://www.tradingview.com',
        'Referer': 'https://www.tradingview.com',
        'Priority': 'u=0, i',
        'Sec-Ch-Ua': '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0',
        'X-Requested-With': 'XMLHttpRequest'
    }
    __ws_timeout = 5
    __request_timeout = 10
    __max_retries = 3
    __retry_delay = 2
    __token_file = Path.home() / 'temp' / 'data_collection' / 'token.txt'
    
    # User-Agent rotation for better evasion
    __user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15',
    ]

    def __init__(
        self,
        username: str = None,
        password: str = None,
        proxy: dict = None,
        random_user_agent: bool = True,
    ) -> None:
        """Create TvDatafeed object

        Args:
            username (str, optional): tradingview username. Defaults to None.
            password (str, optional): tradingview password. Defaults to None.
            proxy (dict, optional): proxy configuration dict with 'http' and/or 'https' keys. 
                                   Example: {'http': 'http://user:pass@host:port', 'https': 'http://user:pass@host:port'}
                                   Defaults to None.
            random_user_agent (bool, optional): Use random user-agent for better evasion. Defaults to True.
        """

        self.ws_debug = False
        self.proxy = proxy
        self.random_user_agent = random_user_agent
        
        # Store credentials for auto-refresh
        self.username = username
        self.password = password

        logger.info("Username: %s", self.username)
        
        # Track last token validation time
        self.last_token_check = None
        self.token_check_interval = 3600  # 1 hour in seconds

        # Try to load cached token first (before any login attempt)
        cached_token = self.__load_token()
        if cached_token:
            logger.info("Found cached token, validating...")
            if self.__validate_token(cached_token):
                self.token = cached_token
                logger.info("✓ Using cached token (skipped login)")
                self.last_token_check = time.time()  # Mark as just checked
            else:
                logger.info("✗ Cached token validation failed, will perform fresh login")
                if username and password:
                    # Need fresh login
                    self.token = self.__auth(username, password)
                    
                    # Save token if login successful
                    if self.token and self.token != "unauthorized_user_token":
                        self.__save_token(self.token)
                        self.last_token_check = time.time()
                else:
                    self.token = None
        elif username and password:
            logger.info("No cached token found, performing fresh login")
            # Need fresh login
            self.token = self.__auth(username, password)
            
            # Save token if login successful
            if self.token and self.token != "unauthorized_user_token":
                self.__save_token(self.token)
                self.last_token_check = time.time()
        else:
            self.token = None

        if self.token is None:
            self.token = "unauthorized_user_token"
            logger.warning(
                "you are using nologin method, data you access may be limited"
            )

        self.ws = None
        self.session = self.__generate_session()
        self.chart_session = self.__generate_chart_session()
    
    def __save_token(self, token):
        """Save token to file"""
        try:
            # Create directory if it doesn't exist
            self.__token_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write token
            with open(self.__token_file, 'w') as f:
                f.write(token)
            
            # Set secure permissions (owner read/write only)
            import os
            try:
                os.chmod(self.__token_file, 0o600)
            except:
                pass  # May fail on Windows
            
            logger.info("Token saved to %s", self.__token_file)
        except Exception as e:
            logger.warning("Failed to save token: %s", e)
    
    def __load_token(self):
        """Load token from file"""
        try:
            if self.__token_file.exists():
                with open(self.__token_file, 'r') as f:
                    token = f.read().strip()
                if token:
                    logger.debug("Token loaded from %s", self.__token_file)
                    return token
        except Exception as e:
            logger.debug("Failed to load token: %s", e)
        return None
    
    def __validate_token(self, token):
        """
        Validate if token is still working.
        Instead of API call, just check if token exists and is not the unauthorized placeholder.
        Real validation happens when websocket connection is made during get_hist.
        """
        if not token or token == "unauthorized_user_token":
            return False
        
        # Basic JWT structure check (should have 3 parts)
        parts = token.split('.')
        if len(parts) != 3:
            logger.debug("Token validation failed: Invalid JWT structure")
            return False
        
        # Try to decode and check expiry
        try:
            import base64
            payload_part = parts[1]
            # Add padding if needed
            padding = len(payload_part) % 4
            if padding:
                payload_part += '=' * (4 - padding)
            
            payload = json.loads(base64.urlsafe_b64decode(payload_part))
            
            # Check if token is expired
            if 'exp' in payload:
                import datetime
                exp_timestamp = payload['exp']
                now_timestamp = datetime.datetime.now().timestamp()
                
                if now_timestamp >= exp_timestamp:
                    logger.debug("Token validation failed: Token expired")
                    return False
                else:
                    time_left = exp_timestamp - now_timestamp
                    logger.debug("Token valid, expires in %.1f hours", time_left / 3600)
                    return True
            else:
                # No expiry in token, assume valid
                logger.debug("Token has no expiry field, assuming valid")
                return True
                
        except Exception as e:
            logger.debug("Token validation error: %s", e)
            # If we can't decode, assume token is still usable
            # Real validation will happen on websocket connection
            return True
    
    def __check_and_refresh_token(self):
        """
        Check token validity periodically and refresh if needed.
        Only checks once per hour to avoid overhead.
        """
        current_time = time.time()
        
        # Check if we need to validate (1 hour has passed)
        if self.last_token_check is None or (current_time - self.last_token_check) >= self.token_check_interval:
            logger.debug("Performing periodic token validation check (1 hour interval)")
            
            # Validate current token
            if not self.__validate_token(self.token):
                logger.warning("Token validation failed - token may be expired")
                
                # Try to refresh if credentials are available
                if self.username and self.password:
                    logger.info("Attempting automatic token refresh...")
                    new_token = self.__auth(self.username, self.password)
                    
                    if new_token and new_token != "unauthorized_user_token":
                        self.token = new_token
                        self.__save_token(new_token)
                        logger.info("✓ Token refreshed successfully")
                        self.last_token_check = current_time
                    else:
                        logger.error("✗ Token refresh failed - continuing with existing token")
                        # IMPORTANT: Update last_token_check even on failure to avoid hammering the API
                        self.last_token_check = current_time
                else:
                    logger.warning("Cannot refresh token - no credentials stored")
                    # Update last_token_check to avoid repeated checks
                    self.last_token_check = current_time
            else:
                logger.debug("Token validation successful")
                self.last_token_check = current_time

    def __auth(self, username, password):

        if (username is None or password is None):
            token = None

        else:
            data = {"username": username,
                    "password": password,
                    "remember": "on"}
            
            token = None
            session = requests.Session()
            
            # Set proxy if provided
            if self.proxy:
                session.proxies.update(self.proxy)
                logger.info("Using proxy for signin: %s", list(self.proxy.keys()))
            
            # Select random user agent if enabled
            user_agent = random.choice(self.__user_agents) if self.random_user_agent else self.__signin_headers['User-Agent']
            logger.debug("Using User-Agent: %s", user_agent[:50] + "...")
            
            # First, load the TradingView homepage to get cookies and establish session
            try:
                logger.debug("Loading TradingView homepage to establish session...")
                
                # Add random delay (0.5-2 seconds) to mimic human behavior
                initial_delay = random.uniform(0.5, 2.0)
                logger.debug("Waiting %.2f seconds before loading homepage (human-like)", initial_delay)
                time.sleep(initial_delay)
                
                homepage_response = session.get(
                    'https://www.tradingview.com',
                    headers={
                        'User-Agent': user_agent,
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-User': '?1',
                        'Upgrade-Insecure-Requests': '1',
                        'Cache-Control': 'max-age=0'
                    },
                    timeout=self.__request_timeout
                )
                logger.debug("Homepage loaded, status: %s, cookies: %s", 
                           homepage_response.status_code, 
                           len(session.cookies))
                
                # Add another human-like delay before signin
                pre_signin_delay = random.uniform(1.0, 3.0)
                logger.debug("Waiting %.2f seconds before signin attempt (human-like)", pre_signin_delay)
                time.sleep(pre_signin_delay)
                
            except Exception as e:
                logger.warning("Failed to load homepage (continuing anyway): %s", e)
            
            for attempt in range(1, self.__max_retries + 1):
                try:
                    logger.debug("Signin attempt %s/%s", attempt, self.__max_retries)
                    
                    # Update headers with selected user agent
                    signin_headers = self.__signin_headers.copy()
                    signin_headers['User-Agent'] = user_agent
                    
                    response = session.post(
                        url=self.__sign_in_url, 
                        data=data, 
                        headers=signin_headers,
                        timeout=self.__request_timeout
                    )
                    
                    logger.debug("Response status code: %s", response.status_code)
                    
                    if response.status_code == 200:
                        try:
                            response_json = response.json()
                            
                            # Check if signin was successful
                            if 'user' in response_json and 'auth_token' in response_json['user']:
                                token = response_json['user']['auth_token']
                                logger.info(response_json)
                                logger.info("Successfully signed in to TradingView")
                                break
                            else:
                                # Check for error messages in response
                                if 'error' in response_json:
                                    logger.error("Signin failed: %s", response_json['error'])
                                else:
                                    logger.error("Signin failed: Unexpected response format - %s", response_json)
                                    
                        except json.JSONDecodeError as je:
                            logger.error("Failed to parse JSON response: %s", je)
                            logger.debug("Response text: %s", response.text[:500])  # Log first 500 chars
                            
                    elif response.status_code == 429:
                        logger.warning("Rate limited (429). Waiting %s seconds before retry...", self.__retry_delay * attempt)
                        time.sleep(self.__retry_delay * attempt)
                        continue
                        
                    else:
                        logger.error("Signin request failed with status code: %s", response.status_code)
                        logger.debug("Response text: %s", response.text[:500])
                        
                except requests.exceptions.Timeout:
                    logger.error("Signin request timed out after %s seconds", self.__request_timeout)
                    
                except requests.exceptions.ConnectionError as ce:
                    logger.error("Connection error during signin: %s", ce)
                    
                except requests.exceptions.RequestException as re:
                    logger.error("Request error during signin: %s", re)
                    
                except KeyError as ke:
                    logger.error("Key error parsing response (check credentials): %s", ke)
                    
                except Exception as e:
                    logger.error("Unexpected error during signin: %s - %s", type(e).__name__, e)
                
                # Wait before retrying (except on last attempt)
                if attempt < self.__max_retries and token is None:
                    # Add jitter to retry delay (±20%)
                    jitter = random.uniform(0.8, 1.2)
                    actual_delay = self.__retry_delay * jitter
                    logger.info("Retrying in %.2f seconds...", actual_delay)
                    time.sleep(actual_delay)

        return token

    def __create_connection(self):
        logging.debug("creating websocket connection")
        self.ws = create_connection(
            "wss://data.tradingview.com/socket.io/websocket", headers=self.__ws_headers, timeout=self.__ws_timeout
        )

    @staticmethod
    def __filter_raw_message(text):
        try:
            found = re.search('"m":"(.+?)",', text).group(1)
            found2 = re.search('"p":(.+?"}"])}', text).group(1)

            return found, found2
        except AttributeError:
            logger.error("error in filter_raw_message")

    @staticmethod
    def __generate_session():
        stringLength = 12
        letters = string.ascii_lowercase
        random_string = "".join(random.choice(letters)
                                for i in range(stringLength))
        return "qs_" + random_string

    @staticmethod
    def __generate_chart_session():
        stringLength = 12
        letters = string.ascii_lowercase
        random_string = "".join(random.choice(letters)
                                for i in range(stringLength))
        return "cs_" + random_string

    @staticmethod
    def __prepend_header(st):
        return "~m~" + str(len(st)) + "~m~" + st

    @staticmethod
    def __construct_message(func, param_list):
        return json.dumps({"m": func, "p": param_list}, separators=(",", ":"))

    def __create_message(self, func, paramList):
        return self.__prepend_header(self.__construct_message(func, paramList))

    def __send_message(self, func, args):
        m = self.__create_message(func, args)
        if self.ws_debug:
            print(m)
        self.ws.send(m)

    @staticmethod
    def __create_df(raw_data, symbol):
        try:
            out = re.search('"s":\[(.+?)\}\]', raw_data).group(1)
            x = out.split(',{"')
            data = list()
            volume_data = True

            for xi in x:
                xi = re.split("\[|:|,|\]", xi)
                ts = datetime.datetime.fromtimestamp(float(xi[4]))

                row = [ts]

                for i in range(5, 10):

                    # skip converting volume data if does not exists
                    if not volume_data and i == 9:
                        row.append(0.0)
                        continue
                    try:
                        row.append(float(xi[i]))

                    except ValueError:
                        volume_data = False
                        row.append(0.0)
                        logger.debug('no volume data')

                data.append(row)

            data = pd.DataFrame(
                data, columns=["datetime", "open",
                               "high", "low", "close", "volume"]
            ).set_index("datetime")
            data.insert(0, "symbol", value=symbol)
            return data
        except AttributeError:
            logger.debug("no data, please check the exchange and symbol")

    @staticmethod
    def __format_symbol(symbol, exchange, contract: int = None):

        if ":" in symbol:
            pass
        elif contract is None:
            symbol = f"{exchange}:{symbol}"

        elif isinstance(contract, int):
            symbol = f"{exchange}:{symbol}{contract}!"

        else:
            raise ValueError("not a valid contract")

        return symbol

    def get_hist(
        self,
        symbol: str,
        exchange: str = "NSE",
        interval: Interval = Interval.in_daily,
        n_bars: int = 10,
        fut_contract: int = None,
        extended_session: bool = False,
    ) -> pd.DataFrame:
        """get historical data

        Args:
            symbol (str): symbol name
            exchange (str, optional): exchange, not required if symbol is in format EXCHANGE:SYMBOL. Defaults to None.
            interval (str, optional): chart interval. Defaults to 'D'.
            n_bars (int, optional): no of bars to download, max 5000. Defaults to 10.
            fut_contract (int, optional): None for cash, 1 for continuous current contract in front, 2 for continuous next contract in front . Defaults to None.
            extended_session (bool, optional): regular session if False, extended session if True, Defaults to False.

        Returns:
            pd.Dataframe: dataframe with sohlcv as columns
        """
        symbol = self.__format_symbol(
            symbol=symbol, exchange=exchange, contract=fut_contract
        )

        interval = interval.value

        self.__create_connection()

        self.__send_message("set_auth_token", [self.token])
        self.__send_message("chart_create_session", [self.chart_session, ""])
        self.__send_message("quote_create_session", [self.session])
        self.__send_message(
            "quote_set_fields",
            [
                self.session,
                "ch",
                "chp",
                "current_session",
                "description",
                "local_description",
                "language",
                "exchange",
                "fractional",
                "is_tradable",
                "lp",
                "lp_time",
                "minmov",
                "minmove2",
                "original_name",
                "pricescale",
                "pro_name",
                "short_name",
                "type",
                "update_mode",
                "volume",
                "currency_code",
                "rchp",
                "rtc",
            ],
        )

        self.__send_message(
            "quote_add_symbols", [self.session, symbol,
                                  {"flags": ["force_permission"]}]
        )
        self.__send_message("quote_fast_symbols", [self.session, symbol])

        self.__send_message(
            "resolve_symbol",
            [
                self.chart_session,
                "symbol_1",
                '={"symbol":"'
                + symbol
                + '","adjustment":"splits","session":'
                + ('"regular"' if not extended_session else '"extended"')
                + "}",
            ],
        )
        self.__send_message(
            "create_series",
            [self.chart_session, "s1", "s1", "symbol_1", interval, n_bars],
        )
        self.__send_message("switch_timezone", [
                            self.chart_session, "exchange"])

        raw_data = ""

        logger.debug("getting data for %s...", symbol)
        while True:
            try:
                result = self.ws.recv()
                raw_data = raw_data + result + "\n"
            except Exception as e:
                logger.debug(e)
                break

            if "series_completed" in result:
                break

        return self.__create_df(raw_data, symbol)

    def search_symbol(self, text: str, exchange: str = ''):
        url = self.__search_url.format(text, exchange)

        symbols_list = []
        try:
            # Use proxy if configured
            proxies = self.proxy if hasattr(self, 'proxy') and self.proxy else None
            
            resp = requests.get(url, timeout=self.__request_timeout, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }, proxies=proxies)
            
            if resp.status_code == 200:
                symbols_list = json.loads(resp.text.replace(
                    '</em>', '').replace('<em>', ''))
                logger.debug("Found %s symbols for '%s'", len(symbols_list), text)
            else:
                logger.error("Symbol search failed with status code: %s", resp.status_code)
                
        except requests.exceptions.Timeout:
            logger.error("Symbol search timed out after %s seconds", self.__request_timeout)
        except requests.exceptions.RequestException as e:
            logger.error("Request error during symbol search: %s", e)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse symbol search response: %s", e)
        except Exception as e:
            logger.error("Unexpected error during symbol search: %s - %s", type(e).__name__, e)

        return symbols_list


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    tv = TvDatafeed()
    print(tv.get_hist("CRUDEOIL", "MCX", fut_contract=1))
    print(tv.get_hist("NIFTY", "NSE", fut_contract=1))
    print(
        tv.get_hist(
            "EICHERMOT",
            "NSE",
            interval=Interval.in_1_hour,
            n_bars=500,
            extended_session=False,
        )
    )
