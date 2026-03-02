"""Module providing a function for 5 paise"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105


from io import StringIO
import time
from datetime import datetime
import traceback
import logging
import pandas as pd
from py5paisa import FivePaisaClient
#import py5paisa
import pyotp
import requests
import fivepaisa.credentials_2 as credentials_leelu
import fivepaisa.credentials_3 as credentials_avanthi
import TelegramSend

logger = logging.getLogger(__name__)

# Enable detailed diagnostics by setting environment variable: FIVEPAISA_DEBUG=1
ENABLE_DIAGNOSTICS = False

commodity_to_symbol = {
    'CRUDEOIL': 'CRUDEOILM',
    'NATURALGAS': 'NATGASMINI',
    'COPPER': 'COPPER',
    'GOLD': 'GOLDM',
    'LEAD': 'LEADMINI',
    'SILVER': 'SILVERMIC',
    'ZINC': 'ZINCMINI',
    'ALUMINIUM': 'ALUMINI',
    'NIFTY': 'NIFTY',
    'BANKNIFTY': 'BANKNIFTY',
    'FINNIFTY': 'FINNIFTY'
}


symbol_to_lot = {
    'CRUDEOIL': 10,
    'NATURALGAS': 250,
    'COPPER': 2500,
    'GOLD': 100,
    'LEAD': 1000,
    'ZINC': 1000,
    'ALUMINIUM': 1000,
    'SILVER': 1000,
}

class fivepaise_api(object):

    def __init__(self, account):
        # create object of call
        # Store account name to fix py5paisa library's shared class variables bug
        self.account = account

        if account == 'leelu':
            cred={
                "APP_NAME":credentials_leelu.APP_NAME,
                "APP_SOURCE":credentials_leelu.APP_SOURCE,
                "USER_ID":credentials_leelu.USER_ID,
                "PASSWORD":credentials_leelu.PASSWORD,
                "USER_KEY":credentials_leelu.USER_KEY,
                "ENCRYPTION_KEY":credentials_leelu.ENCRYPTION_KEY,
            }
        elif account == 'avanthi':
            cred={
                "APP_NAME":credentials_avanthi.APP_NAME,
                "APP_SOURCE":credentials_avanthi.APP_SOURCE,
                "USER_ID":credentials_avanthi.USER_ID,
                "PASSWORD":credentials_avanthi.PASSWORD,
                "USER_KEY":credentials_avanthi.USER_KEY,
                "ENCRYPTION_KEY":credentials_avanthi.ENCRYPTION_KEY,
            }
        else:
            print("Invalid account")
            return

        self.obj = FivePaisaClient(cred=cred)

        # CRITICAL FIX: Immediately fix the shared class variable after creating FivePaisaClient
        # This prevents the payload from being corrupted by another account's initialization
        if account == 'leelu':
            self.obj.login_check_payload = {
                'head': {
                    'requestCode': '5PLoginCheck',
                    'key': credentials_leelu.USER_KEY,
                    'appVer': '1.0',
                    'appName': credentials_leelu.APP_NAME,
                    'osName': 'WEB',
                    'LoginId': credentials_leelu.CLIENTCODE
                },
                'body': {
                    'RegistrationID': ''  # Will be set after getting session
                }
            }
        elif account == 'avanthi':
            self.obj.login_check_payload = {
                'head': {
                    'requestCode': '5PLoginCheck',
                    'key': credentials_avanthi.USER_KEY,
                    'appVer': '1.0',
                    'appName': credentials_avanthi.APP_NAME,
                    'osName': 'WEB',
                    'LoginId': credentials_avanthi.CLIENTCODE
                },
                'body': {
                    'RegistrationID': ''  # Will be set after getting session
                }
            }

        attempts = 5
        while attempts > 0:
            if account == 'leelu':
                totp_pin = pyotp.TOTP(credentials_leelu.TOTP).now()

                self.session = self.obj.get_totp_session(credentials_leelu.CLIENTCODE,totp_pin,credentials_leelu.PIN)
                if self.session:
                    # CRITICAL FIX: Update payload with session immediately after getting it
                    self.obj.login_check_payload['body']['RegistrationID'] = self.session
                    self.obj.login_check_payload['head']['LoginId'] = credentials_leelu.CLIENTCODE
                    self.obj.login_check_payload['head']['key'] = credentials_leelu.USER_KEY
                    self.obj.login_check_payload['head']['appName'] = credentials_leelu.APP_NAME

                    if None is self.obj.Login_check():
                        print("Login failed")
                        continue
                    # CRITICAL FIX: Fix payload again after Login_check (it might have been corrupted)
                    self.obj.login_check_payload['head']['LoginId'] = credentials_leelu.CLIENTCODE
                    self.obj.login_check_payload['head']['key'] = credentials_leelu.USER_KEY
                    self.obj.login_check_payload['head']['appName'] = credentials_leelu.APP_NAME
                    self.obj.login_check_payload['body']['RegistrationID'] = self.session
                    break
            if account == 'avanthi':
                totp_pin = pyotp.TOTP(credentials_avanthi.TOTP).now()

                self.session = self.obj.get_totp_session(credentials_avanthi.CLIENTCODE,totp_pin,credentials_avanthi.PIN)
                if self.session:
                    # CRITICAL FIX: Update payload with session immediately after getting it
                    self.obj.login_check_payload['body']['RegistrationID'] = self.session
                    self.obj.login_check_payload['head']['LoginId'] = credentials_avanthi.CLIENTCODE
                    self.obj.login_check_payload['head']['key'] = credentials_avanthi.USER_KEY
                    self.obj.login_check_payload['head']['appName'] = credentials_avanthi.APP_NAME
                    break
            attempts = attempts - 1
            time.sleep(30)

        self.intializeSymbolTokenMap()

        # Print diagnostics after initialization if enabled
        if ENABLE_DIAGNOSTICS:
            self._print_account_diagnostics()

    def _print_account_diagnostics(self):
        """Print diagnostic information about the account and FivePaisaClient state"""
        separator = "="*80
        print(f"\n{separator}")
        print(f"DIAGNOSTIC INFO FOR ACCOUNT: {self.account}")
        print(separator)

        logger.info(f"[{self.account}] ════════════════════════════════════════════════════════════")
        logger.info(f"[{self.account}] DIAGNOSTIC INFO FOR ACCOUNT: {self.account}")
        logger.info(f"[{self.account}] ════════════════════════════════════════════════════════════")

        # Account info
        print(f"Account: {self.account}")
        print(f"Object ID: {id(self)}")
        print(f"FivePaisaClient ID: {id(self.obj)}")
        print(f"Session ID: {id(self.session)}")

        logger.info(f"[{self.account}] Account: {self.account}")
        logger.info(f"[{self.account}] Object ID: {id(self)}")
        logger.info(f"[{self.account}] FivePaisaClient ID: {id(self.obj)}")
        logger.info(f"[{self.account}] Session ID: {id(self.session)}")

        # Session token
        session_display = f"{self.session[:50]}..." if self.session else "None"
        print(f"Session Token: {session_display}")
        logger.info(f"[{self.account}] Session Token: {session_display}")

        # Client credentials
        print(f"Client Code: {self.obj.client_code}")
        print(f"APP_NAME: {self.obj.APP_NAME}")
        print(f"APP_SOURCE: {self.obj.APP_SOURCE}")
        print(f"USER_KEY: {self.obj.USER_KEY[:20]}...")

        logger.info(f"[{self.account}] Client Code: {self.obj.client_code}")
        logger.info(f"[{self.account}] APP_NAME: {self.obj.APP_NAME}")
        logger.info(f"[{self.account}] APP_SOURCE: {self.obj.APP_SOURCE}")
        logger.info(f"[{self.account}] USER_KEY: {self.obj.USER_KEY[:20]}...")

        # Check login_check_payload
        payload_client = self.obj.login_check_payload.get('head', {}).get('LoginId', 'UNKNOWN')
        payload_app = self.obj.login_check_payload.get('head', {}).get('appName', 'UNKNOWN')
        payload_key = self.obj.login_check_payload.get('head', {}).get('key', 'UNKNOWN')[:20] + "..."

        print("\nlogin_check_payload state:")
        print(f"  LoginId (client_code): {payload_client}")
        print(f"  appName: {payload_app}")
        print(f"  key: {payload_key}")

        logger.info(f"[{self.account}] login_check_payload state:")
        logger.info(f"[{self.account}]   LoginId (client_code): {payload_client}")
        logger.info(f"[{self.account}]   appName: {payload_app}")
        logger.info(f"[{self.account}]   key: {payload_key}")

        # Compare with expected
        if self.account == 'leelu':
            expected_client = credentials_leelu.CLIENTCODE
        elif self.account == 'avanthi':
            expected_client = credentials_avanthi.CLIENTCODE
        else:
            expected_client = "UNKNOWN"

        match = "✓ CORRECT" if payload_client == expected_client else f"✗ WRONG (expected {expected_client})"
        print(f"  Status: {match}")
        print(f"{separator}\n")

        logger.info(f"[{self.account}]   Status: {match}")
        logger.info(f"[{self.account}] ════════════════════════════════════════════════════════════")

        # Summary for easy scanning
        logger.info(f"[{self.account}] SUMMARY: client_code={self.obj.client_code}, payload_client={payload_client}, status={match}")

    def _fix_shared_payload_bug(self):
        """
        Fix py5paisa library's class-level shared variable bug.
        The library has login_check_payload as a class variable that's shared
        between all instances, causing "another client" errors.
        This method updates it with the correct credentials before each order.
        """
        # Log the BEFORE state
        old_client_code = self.obj.login_check_payload.get('head', {}).get('LoginId', 'UNKNOWN')
        logger.debug(f"[{self.account}] BEFORE fix: login_check_payload has client_code={old_client_code}")

        if self.account == 'leelu':
            self.obj.login_check_payload = {
                'head': {
                    'requestCode': '5PLoginCheck',
                    'key': credentials_leelu.USER_KEY,
                    'appVer': '1.0',
                    'appName': credentials_leelu.APP_NAME,
                    'osName': 'WEB',
                    'LoginId': credentials_leelu.CLIENTCODE
                },
                'body': {
                    'RegistrationID': self.session
                }
            }
            logger.debug(f"[{self.account}] AFTER fix: Set login_check_payload to client_code={credentials_leelu.CLIENTCODE}")
        elif self.account == 'avanthi':
            self.obj.login_check_payload = {
                'head': {
                    'requestCode': '5PLoginCheck',
                    'key': credentials_avanthi.USER_KEY,
                    'appVer': '1.0',
                    'appName': credentials_avanthi.APP_NAME,
                    'osName': 'WEB',
                    'LoginId': credentials_avanthi.CLIENTCODE
                },
                'body': {
                    'RegistrationID': self.session
                }
            }
            logger.debug(f"[{self.account}] AFTER fix: Set login_check_payload to client_code={credentials_avanthi.CLIENTCODE}")

        # Print diagnostics after fix if enabled
        if ENABLE_DIAGNOSTICS:
            print(f"\n[AFTER FIX for {self.account}]")
            self._print_account_diagnostics()

    def _refresh_session(self):
        """
        Refresh the session token by getting a new TOTP session.
        This is needed when the session token is tied to the wrong account.
        """
        logger.warning(f"[{self.account}] 🔄 Refreshing session token due to 'another client' error")

        attempts = 3
        while attempts > 0:
            try:
                if self.account == 'leelu':
                    totp_pin = pyotp.TOTP(credentials_leelu.TOTP).now()
                    new_session = self.obj.get_totp_session(credentials_leelu.CLIENTCODE, totp_pin, credentials_leelu.PIN)
                    if new_session:
                        self.session = new_session
                        # Fix the payload with correct credentials and new session
                        self._fix_shared_payload_bug()
                        # Verify login
                        if self.obj.Login_check() is not None:
                            logger.info(f"[{self.account}] ✅ Session refreshed successfully")
                            return True
                elif self.account == 'avanthi':
                    totp_pin = pyotp.TOTP(credentials_avanthi.TOTP).now()
                    new_session = self.obj.get_totp_session(credentials_avanthi.CLIENTCODE, totp_pin, credentials_avanthi.PIN)
                    if new_session:
                        self.session = new_session
                        # Fix the payload with correct credentials and new session
                        self._fix_shared_payload_bug()
                        logger.info(f"[{self.account}] ✅ Session refreshed successfully")
                        return True
            except Exception as e:
                logger.error(f"[{self.account}] ❌ Error refreshing session: {e}")
                logger.error(f"[{self.account}] Traceback: {''.join(traceback.format_exception(e))}")

            attempts -= 1
            if attempts > 0:
                time.sleep(2)

        logger.error(f"[{self.account}] ❌ Failed to refresh session after 3 attempts")
        return False

    def download_csv(self, url):
        response = requests.get(url, timeout=50)
        response.raise_for_status()  # Ensure the download was successful
        return response.text

    def intializeSymbolTokenMap(self):
        try:
            url = "https://openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
            self.csv_data = self.download_csv(url)
            self.scrip_master_df = pd.read_csv(StringIO(self.csv_data))
            self.scrip_master_df.to_csv("scrip_master_5paise.csv")
        except Exception as e:
            print(f"Error: {e}")

            # Check if the file exists
            try:
                self.scrip_master_df = pd.read_csv("scrip_master_5paise.csv")
            except Exception as e1:
                print(f"Error: {e1}")
                print("Error getting scrip_master_5paise.csv")
                raise e1

    def getTokenInfo(self, symbol, strike_price, pe_ce):
        # script_master_df has Name column like FINNIFTY 13 Feb 2024 CE 23750.00
        # Match the symbol and strike price and pe_ce and get ScriptCode collumn value
        df = self.scrip_master_df

        df = df[(df['SymbolRoot'] == symbol) & (df['StrikeRate'] == strike_price) & (df['ScripType'] == pe_ce)]
        # Sort the df by expiry date and get the first row
        df = df.sort_values(by='Expiry')

        # Add validation before accessing
        if df.empty:
            print(f"No matching options found for {symbol} {strike_price} {pe_ce}")
            return None

        # df.iloc[0]['expiry'] is before current date return the next row
        if df.iloc[0]['Expiry'] < datetime.now().strftime('%Y-%m-%d'):
            # Check if there's a next row before accessing
            if len(df) > 1:
                return df.iloc[1]
            else:
                print(f"No future expiry found for {symbol} {strike_price} {pe_ce}")
                return None
        return df.iloc[0]

    def _find_recent_order(self, scrip_code, buy_sell, qty, is_intraday=None):
        """Check order book for a non-rejected order matching scrip_code/side/qty.

        Used after an exception to detect if the order was processed by the
        broker before the client received the error, preventing duplicate orders.

        Args:
            scrip_code: Numeric 5paisa ScripCode
            buy_sell: 'B' or 'S' (already remapped)
            qty: Order quantity (int)
            is_intraday: True for intraday, False for carryforward (None = skip check)

        Returns BrokerOrderId (int) if found, None otherwise.
        """
        try:
            self._fix_shared_payload_bug()
            orderbook = self.obj.order_book()
            if not orderbook:
                return None
            for o in orderbook:
                status = str(o.get('OrderStatus', '')).lower()
                if 'reject' in status or 'cancel' in status:
                    continue
                if (int(o.get('ScripCode', -1)) == int(scrip_code)
                        and str(o.get('BuySell', '')) == str(buy_sell)
                        and int(o.get('Qty', 0)) == int(qty)):
                    if is_intraday is not None and bool(o.get('IsIntraday', False)) != bool(is_intraday):
                        continue
                    broker_id = o.get('BrokerOrderId') or o.get('BrokerOrderID') or o.get('OrderId')
                    if broker_id:
                        return int(broker_id)
        except Exception as e:
            logger.error(f"[{self.account}] Error checking order book for recent order: {e}")
        return None

    def get_commodity_symbol(self, symbol, expiry=None):
        df = self.scrip_master_df

        df = df[(df['SymbolRoot'] == symbol) & (df['ScripType'] == 'XX')]
        # Add validation for empty DataFrame
        if df.empty:
            print(f"No matching commodity found for {symbol}")
            return None

        # Sort the df by expiry date and get the first row
        df = df.sort_values(by='Expiry')

        if expiry is not None:
            df = df[df['Expiry'] == expiry]
            if df.empty:
                return None
            return df.iloc[0]

        # Check if the first expiry is within 10 days
        if len(df) > 1 and pd.to_datetime(df.iloc[0]['Expiry']) - pd.Timestamp.now() <= pd.Timedelta(days=10):
            return df.iloc[1]  # Return the next expiry
        elif len(df) > 0:
            return df.iloc[0]  # Return the first expiry
        else:
            return None

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, isCommodity=True):
        logger.info(f"[{self.account}] 🔵 Placing COMMODITY order: {buy_sell} {symbol} qty={qty} expiry={expiry}")

        # Print diagnostics before order if enabled
        if ENABLE_DIAGNOSTICS:
            print(f"\n[BEFORE COMMODITY ORDER for {self.account}]")
            self._print_account_diagnostics()

        # Fix py5paisa library's shared class variable bug before placing order
        self._fix_shared_payload_bug()

        tokenInfo = self.get_commodity_symbol(commodity_to_symbol[symbol], expiry)
        if tokenInfo is None:
            logger.error(f"[{self.account}] ❌ Could not find token info for {symbol}")
            return -1, -1

        print("five paise place order")

        symbol_name = tokenInfo['SymbolRoot']
        token = tokenInfo['ScripCode']
        lot = int(tokenInfo['LotSize'])

        #qty = qty * lot

        print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol_name}, Token: {token}, Lot: {lot}")

        if buy_sell == 'BUY':
            buy_sell = 'B'
        else:
            buy_sell = 'S'
        try:
            if isCommodity:
                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='M', ExchangeType='D', \
                                                ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=False)
            else:
                qty = qty * lot
                order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', \
                                                ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=True)
            print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            if order_id is None:
                logger.error(f"[{self.account}] ❌ place_order returned None")
                return -1, -1

            broker_order_id = order_id.get('BrokerOrderID', -1)
            message = order_id.get('Message', 'No message')
            
            print(f"Order id: {broker_order_id} {message}")
            logger.info(f"[{self.account}] ✅ COMMODITY Order placed: order_id={broker_order_id} message='{message}'")

            # Check for "another client" error even on success
            if 'another client' in str(message).lower():
                logger.error(f"[{self.account}] ❌ DETECTED 'another client' error! This should NOT happen after fix!")
                logger.error(f"[{self.account}] Current client_code in payload: {self.obj.login_check_payload.get('head', {}).get('LoginId', 'UNKNOWN')}")

                # Try refreshing the session and retrying once
                logger.warning(f"[{self.account}] Attempting to refresh session and retry COMMODITY order...")
                if self._refresh_session():
                    # Retry the order after session refresh
                    time.sleep(1)
                    try:
                        if isCommodity:
                            order_id = self.obj.place_order(OrderType=buy_sell, Exchange='M', ExchangeType='D', \
                                                            ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=False)
                        else:
                            qty = qty * lot
                            order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', \
                                                            ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=True)
                        if order_id is not None:
                            broker_order_id = order_id.get('BrokerOrderID', -1)
                            message = order_id.get('Message', 'No message')
                            logger.info(f"[{self.account}] ✅ COMMODITY Order placed after session refresh: order_id={broker_order_id} message='{message}'")

                            # Check again for "another client" error
                            if 'another client' in str(message).lower():
                                logger.error(f"[{self.account}] ❌ Still getting 'another client' error after session refresh!")
                        else:
                            logger.error(f"[{self.account}] ❌ place_order returned None after session refresh")
                    except Exception as retry_e:
                        logger.error(f"[{self.account}] ❌ Error retrying COMMODITY order after session refresh: {retry_e}")
                else:
                    logger.error(f"[{self.account}] ❌ Could not refresh session, COMMODITY order will fail")
        except Exception as e1:
            try:
                time.sleep(2)
                print(f" Retry order Time: {datetime.now().strftime('%H:%M:%S')})")
                print("Error placing order, trying again")
                logger.error("Error placing order, trying again %s", e1)

                x = TelegramSend.telegram_send_api()
                x.send_message("-4008545231", f"Warning 5 paise {symbol} order Pls check")

                # Before retrying, check if the position already exists at the broker.
                # The order may have been processed despite the exception, and retrying
                # would create a duplicate position.
                # At this point buy_sell has been remapped to 'B'/'S'.
                trade_type = 'long' if buy_sell == 'B' else 'short'
                pos_type, _ = self.get_commodity_position(symbol, trade_type)
                if pos_type is not None:
                    logger.info(f"[{self.account}] Position already exists for {symbol} ({trade_type}) after exception. Skipping retry to avoid duplicate.")
                    return -1, tokenInfo['Expiry'] if tokenInfo is not None else None

                # FIX: Use correct MCX exchange (was incorrectly using 'C'/'C' cash/equity exchange)
                if isCommodity:
                    order_id = self.obj.place_order(OrderType=buy_sell, Exchange='M', ExchangeType='D', \
                                                    ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=False)
                else:
                    order_id = self.obj.place_order(OrderType=buy_sell, Exchange='N', ExchangeType='D', \
                                                    ScripCode=int(token), Qty=int(qty), Price=0, IsIntraday=True)
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                if order_id is not None:
                    broker_order_id = order_id.get('BrokerOrderID', -1)
                    message = order_id.get('Message', 'No message')
                    print(f"Order id: {broker_order_id} {message}")
                    logger.info(f"Order id: {broker_order_id} {message}")
                else:
                    print("Order id: None")
                    logger.error("place_order returned None during retry")
                    return -1, -1
            except Exception as e2:
                print(''.join(traceback.format_exception(e2)))
                print(f"Error executing place_order: {e2}")
                logging.error("Error executing place_order: %s", e2)
                return -1, -1
        if order_id is None:
            return -1, -1
        return order_id.get('BrokerOrderID', -1), tokenInfo['Expiry'] if tokenInfo is not None else None

    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, isIntraday=True):
        """
        Place an order for a given symbol, quantity, buy/sell action, strike price, and option type (PE/CE).
        
        Args:
            symbol: The underlying symbol (e.g., 'NIFTY', 'BANKNIFTY')
            qty: Quantity to trade
            buy_sell: 'BUY' or 'SELL'
            strike_price: Strike price of the option
            pe_ce: Option type 'PE' or 'CE'
            isIntraday: True for intraday orders, False for delivery/positional orders
        """
        logger.info(f"[{self.account}] 🟢 Placing OPTION order: {buy_sell} {symbol} {strike_price} {pe_ce} qty={qty} intraday={isIntraday}")

        # Print diagnostics before order if enabled
        if ENABLE_DIAGNOSTICS:
            print(f"\n[BEFORE OPTION ORDER for {self.account}]")
            self._print_account_diagnostics()

        # Fix py5paisa library's shared class variable bug before placing order
        self._fix_shared_payload_bug()

        tokenInfo = self.getTokenInfo(symbol, strike_price, pe_ce)

        if symbol == "SENSEX":
            # For SENSEX, we use the BFO exchange
            exchange = "B"
        else:
            # For other symbols, we use the NFO exchange
            exchange = "N"

        if tokenInfo is None:
            print(f"Could not find token info for {symbol} {strike_price} {pe_ce}")
            return -1, None

        print("five paise place order")

        symbol = tokenInfo['SymbolRoot']
        token = tokenInfo['ScripCode']
        lot = int(tokenInfo['LotSize'])

        print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {symbol}, Token: {token}, Lot: {lot}, IsIntraday: {isIntraday}")

        if qty % lot != 0:
            return -1, None

        if buy_sell == 'BUY':
            buy_sell = 'B'
        else:
            buy_sell = 'S'

        try:
            # Use the isIntraday parameter in the order placement
            order_id = self.obj.place_order(
                OrderType=buy_sell,
                Exchange=exchange,
                ExchangeType='D',
                ScripCode=int(token),
                Qty=int(qty),
                Price=0,
                IsIntraday=isIntraday  # Actually use the parameter
            )
            print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            if order_id is None:
                logger.error(f"[{self.account}] ❌ place_order returned None")
                return -1, None
                
            broker_order_id = order_id.get('BrokerOrderID', -1)
            message = order_id.get('Message', 'No message')
            print(f"Order id: {broker_order_id} {message}")
            logger.info(f"[{self.account}] ✅ OPTION Order placed: order_id={broker_order_id} message='{message}'")

            # Check for "another client" error even on success
            if 'another client' in str(message).lower():
                logger.error(f"[{self.account}] ❌ DETECTED 'another client' error! This should NOT happen after fix!")
                logger.error(f"[{self.account}] Current client_code in payload: {self.obj.login_check_payload.get('head', {}).get('LoginId', 'UNKNOWN')}")

                # Try refreshing the session and retrying once
                logger.warning(f"[{self.account}] Attempting to refresh session and retry OPTION order...")
                if self._refresh_session():
                    # Retry the order after session refresh
                    time.sleep(1)
                    try:
                        order_id = self.obj.place_order(
                            OrderType=buy_sell,
                            Exchange='N',
                            ExchangeType='D',
                            ScripCode=int(token),
                            Qty=int(qty),
                            Price=0,
                            IsIntraday=isIntraday
                        )
                        logger.info(f"[{self.account}] ✅ OPTION Order placed after session refresh: order_id={order_id['BrokerOrderID']} message='{order_id['Message']}'")

                        # Check again for "another client" error
                        if 'another client' in str(order_id.get('Message', '')).lower():
                            logger.error(f"[{self.account}] ❌ Still getting 'another client' error after session refresh!")
                    except Exception as retry_e:
                        logger.error(f"[{self.account}] ❌ Error retrying OPTION order after session refresh: {retry_e}")
                else:
                    logger.error(f"[{self.account}] ❌ Could not refresh session, OPTION order will fail")

        except Exception as e1:
            try:
                time.sleep(2)
                print(f" Retry order Time: {datetime.now().strftime('%H:%M:%S')})")
                print("Error placing order, trying again")
                logger.error("Error placing order, trying again %s", e1)

                x = TelegramSend.telegram_send_api()
                x.send_message("-4008545231", f"Warning 5 paise {symbol} order Pls check")

                # Before retrying, check if the order was already placed at the broker.
                # The exception may have fired after the order was processed server-side.
                existing_order_id = self._find_recent_order(int(token), buy_sell, int(qty), isIntraday)
                if existing_order_id:
                    logger.info(f"[{self.account}] Order {existing_order_id} already exists for {symbol} after exception. Skipping retry.")
                    broker_order_id = existing_order_id
                    order_id = {'BrokerOrderID': existing_order_id, 'Message': 'found in orderbook'}
                else:
                    # Retry with the same isIntraday parameter
                    order_id = self.obj.place_order(
                        OrderType=buy_sell,
                        Exchange='N',
                        ExchangeType='D',
                        ScripCode=int(token),
                        Qty=int(qty),
                        Price=0,
                        IsIntraday=isIntraday  # Use the parameter in retry as well
                    )
                print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                if order_id is not None:
                    broker_order_id = order_id.get('BrokerOrderID', -1)
                    message = order_id.get('Message', 'No message')
                    print(f"Order id: {broker_order_id} {message}")
                    logger.info(f"Order id: {broker_order_id} {message}")
                else:
                    print("Order id: None")
                    logger.error("place_order returned None during retry")

            except Exception as e2:
                print(''.join(traceback.format_exception(e2)))
                print(f"Error executing place_order: {e2}")
                logging.error("Error executing place_order: %s", e2)
                return -1, None

        if order_id is None:
            return -1, None
        return order_id.get('BrokerOrderID', -1), tokenInfo['Expiry'] if tokenInfo is not None else None

    def place_order_synthetic_future(self, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):
        """
        Place an order for synthetic futures.
        Returns: (order_id, expiry_date) tuple or (-1, None) on failure
        """
        logger.info(f"[{self.account}] 🔵 Placing SYNTHETIC FUTURE order: {buy_sell} {symbol} {strike_price} {pe_ce} qty={qty} expiry={expiry}")

        # Fix py5paisa library's shared class variable bug before placing order
        self._fix_shared_payload_bug()

        try:
            # 1. GET TOKEN INFO
            df = self.scrip_master_df
            df = df[(df['SymbolRoot'] == symbol) & (df['StrikeRate'] == strike_price) & (df['ScripType'] == pe_ce)]

            if df.empty:
                logger.error(f"No matching contracts found for {symbol} {strike_price} {pe_ce}")
                return -1, None

            df = df.sort_values(by='Expiry')

            # 2. DETERMINE EXPIRY
            tokenInfo = None
            today_str = datetime.now().strftime('%Y-%m-%d')

            if expiry is not None:
                # Use specified expiry
                matching_expiry = df[df['Expiry'] == expiry]
                if matching_expiry.empty:
                    logger.error(f"No contract found for specified expiry {expiry}")
                    return -1, None
                tokenInfo = matching_expiry.iloc[0]
            else:
                # Find appropriate expiry - get the next available one at least 8 days out
                today = datetime.now()
                # Use .copy() to avoid SettingWithCopyWarning
                df_copy = df.copy()
                df_copy['ExpiryDate'] = pd.to_datetime(df_copy['Expiry'])
                future_expiries = df_copy[df_copy['ExpiryDate'] > (today + pd.Timedelta(days=8))]

                if future_expiries.empty:
                    # If none > 8 days, just take the first one that is >= today
                    valid_expiries = df[df['Expiry'] >= today_str]
                    if valid_expiries.empty:
                        logger.error(f"No valid future expiries found for {symbol}")
                        return -1, None
                    tokenInfo = valid_expiries.iloc[0]
                else:
                    tokenInfo = future_expiries.iloc[0]

            # 3. PREPARE ORDER PARAMETERS
            token = tokenInfo['ScripCode']
            lot = int(tokenInfo['LotSize'])

            if symbol == "SENSEX":
                exchange = "B"
            else:
                exchange = "N"

            logger.info(f"Selected contract: {tokenInfo['Name']}, scrip_code: {token}, expiry: {tokenInfo['Expiry']}")

            if qty % lot != 0:
                logger.error(f"Quantity {qty} not multiple of lot size {lot}")
                return -1, None

            if buy_sell == 'BUY':
                order_type = 'B'
            else:
                order_type = 'S'

            # 4. PLACE ORDER
            order_id = self.obj.place_order(
                OrderType=order_type,
                Exchange=exchange,
                ExchangeType='D',
                ScripCode=int(token),
                Qty=int(qty),
                Price=0,
                IsIntraday=False # Synthetic futures are usually positional
            )

            print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
            print(f"Order id: {order_id['BrokerOrderID']} {order_id['Message']}")
            logger.info(f"[{self.account}] ✅ SYNTHETIC FUTURE Order placed: order_id={order_id['BrokerOrderID']} message='{order_id['Message']}'")

            # Check for "another client" error
            if 'another client' in str(order_id.get('Message', '')).lower():
                logger.error(f"[{self.account}] ❌ DETECTED 'another client' error during synthetic future order!")
                if self._refresh_session():
                    time.sleep(1)
                    order_id = self.obj.place_order(
                        OrderType=order_type,
                        Exchange=exchange,
                        ExchangeType='D',
                        ScripCode=int(token),
                        Qty=int(qty),
                        Price=0,
                        IsIntraday=False
                    )
                    logger.info(f"[{self.account}] ✅ SYNTHETIC FUTURE Order placed after refresh: order_id={order_id['BrokerOrderID']}")

            return order_id['BrokerOrderID'], tokenInfo['Expiry']

        except Exception as e:
            logger.error(f"Fatal error in place_order_synthetic_future: {e}")
            print(''.join(traceback.format_exception(e)))
            return -1, None

    def get_commodity_position(self, symbol, trade_type):
        """Check if there is an open MCX commodity position matching the intended trade.

        Args:
            symbol: Commodity display name ('GOLD', 'SILVER', 'COPPER', etc.)
            trade_type: 'long' or 'short'

        Returns:
            (trade_type, avg_price) if a matching position is found, (None, 0) otherwise
        """
        try:
            self._fix_shared_payload_bug()
            positions = self.obj.positions()

            if not positions:
                logger.info(f"[{self.account}] No open positions returned for {symbol}")
                return None, 0

            symbol_prefix_map = {
                'GOLD': 'GOLDM',
                'SILVER': 'SILVERMIC',
                'COPPER': 'COPPER',
                'CRUDEOIL': 'CRUDEOILM',
                'NATURALGAS': 'NATGASMINI',
                'LEAD': 'LEADMINI',
                'ZINC': 'ZINCMINI',
                'ALUMINIUM': 'ALUMINI',
            }
            mcx_prefix = symbol_prefix_map.get(symbol, symbol)

            for pos in positions:
                # 5paisa uses Exch='M' for MCX derivatives
                exch = pos.get('Exch', '')
                if exch != 'M':
                    continue
                scrip_name = str(pos.get('ScripName', '') or pos.get('Scrip', '') or '')
                if not scrip_name.upper().startswith(mcx_prefix.upper()):
                    continue

                net_qty = int(pos.get('NetQty', 0))
                if net_qty > 0 and trade_type == 'long':
                    # AvgRate is the carry-forward avg price; BuyAvgRate is only non-zero for same-day buys
                    avg_price = (float(pos.get('AvgRate', 0) or 0)
                                 or float(pos.get('BuyAvgRate', 0) or 0))
                    logger.info(f"[{self.account}] Found LONG position for {symbol}: qty={net_qty} avg={avg_price}")
                    return 'long', avg_price
                elif net_qty < 0 and trade_type == 'short':
                    avg_price = (float(pos.get('AvgRate', 0) or 0)
                                 or float(pos.get('SellAvgRate', 0) or 0))
                    logger.info(f"[{self.account}] Found SHORT position for {symbol}: qty={net_qty} avg={avg_price}")
                    return 'short', avg_price

            logger.info(f"[{self.account}] No matching {trade_type} position found for {symbol}")
            return None, 0
        except Exception as e:
            logger.error(f"[{self.account}] Error checking 5paisa commodity position for {symbol}: {e}")
            return None, 0

    def get_ledger_balance(self):
        """Fetch the ledger balance for the account"""
        try:
            self._fix_shared_payload_bug()
            margin_data = self.obj.margin()
            if margin_data and len(margin_data) > 0:
                # margin() returns a list of dictionaries, usually one per segment
                # Ledgerbalance is what the user is looking for
                balance = margin_data[0].get('Ledgerbalance', 0)
                logger.info(f"[{self.account}] Ledger balance: {balance}")
                return float(balance)
            return 0.0
        except Exception as e:
            logger.error(f"[{self.account}] Error fetching ledger balance: {e}")
            return 0.0

    def get_order_status(self, order_id):
        try:
            logger.info(f"[{self.account}] 🔍 Checking order status for order_id={order_id}")

            # Fix py5paisa library's shared class variable bug before checking status
            self._fix_shared_payload_bug()

            # Early return for invalid order IDs
            if order_id is None or order_id == 0 or order_id == -1:
                print(f"Invalid order_id: {order_id}. Cannot check status.")
                logger.error(f"Invalid order_id: {order_id}. Cannot check status.")
                return "InvalidID", -1

            #orderbook = self.obj.orderBook()['OrderBookDetail']
            print(order_id)
            try :
                orderbook = self.obj.order_book()
            except Exception as e:
                try:
                    print("Error getting orderbook, trying again")
                    print(f"Error: {e}")
                    logger.error("Error getting orderbook, trying again %s", e)
                    time.sleep(2)
                    orderbook = self.obj.order_book()
                except Exception as e1:
                    print(f"Error: {e1}")
                    logger.error("Error getting orderbook, trying again %s", e1)
                    return -1, -1

            orderbook = pd.DataFrame(orderbook)

            # Find the correct column name for broker order ID
            broker_order_col = None
            for col in ['BrokerOrderId', 'BrokerOrderID', 'brokerOrderId', 'OrderId', 'ExchOrderID']:
                if col in orderbook.columns:
                    broker_order_col = col
                    break

            if broker_order_col is None:
                print(f"BrokerOrderId column not found. Available columns: {orderbook.columns.tolist()}")
                logger.error(f"BrokerOrderId column not found. Available columns: {orderbook.columns.tolist()}")
                return -1, -1

            # Check if order exists - use bracket notation to avoid AttributeError
            matching_orders = orderbook[orderbook[broker_order_col] == order_id]
            if matching_orders.empty:
                print(f"Order {order_id} not found in orderbook")
                logger.warning(f"Order {order_id} not found in orderbook")
                return "NotFound", -1

            order_status = matching_orders['OrderStatus'].values[0]
            average_price = matching_orders['AveragePrice'].values[0]

            # Log raw status for debugging
            logger.info(f"[{self.account}] Raw order status for {order_id}: '{order_status}'")

            # Case-insensitive status matching
            order_status_lower = str(order_status).lower()
            if order_status_lower in ('fully executed', 'complete'):
                order_ret = "Complete"
            elif order_status_lower in ('open', 'pending', 'ordered',
                                        'partially executed', 'after market order req received'):
                order_ret = "Open"
            elif 'rejected' in order_status_lower or 'cancelled' in order_status_lower:
                order_ret = "Rejected"
            else:
                # Unknown status - log warning and treat as Open (don't retry)
                logger.warning(f"[{self.account}] Unknown order status '{order_status}' for order {order_id}, treating as Open")
                order_ret = "Open"

            logger.info(f"[{self.account}] 📊 Order status result: order_id={order_id} status={order_ret} price={average_price}")
            return order_ret, average_price
        except Exception as e:
            print(''.join(traceback.format_exception(e)))
            print(f"Error executing get_order_status: {e}")
            logger.error("Error executing get_order_status: %s", e)
            return -1, -1


"""
print("Starting")
angel_obj = fivepaise_api("avanthi")
print("Object created")
#orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')
orderid, expiry_gold = angel_obj.place_order_commodity('GOLD', 3, 'BUY', None)

print(orderid)
print(expiry_gold)

orderid, expiry = angel_obj.place_order_synthetic_future('BANKNIFTY', 15, 'BUY', 52000, 'CE')

print(f"Synthetic Future Order ID: {orderid}, Expiry: {expiry}")

orderid, expiry = angel_obj.place_order_commodity('SILVER', 3, 'BUY', None)

print(orderid)
print(expiry)

orderid, expiry = angel_obj.place_order_commodity('ALUMINIUM', 3, 'BUY', None)

print(orderid)
print(expiry)

orderid, expiry = angel_obj.place_order_commodity('CRUDEOIL', 3, 'BUY', None)

print(orderid)
print(expiry)



orderid, expiry = angel_obj.place_order_commodity('GOLD', 3, 'SELL', expiry_gold)

print(orderid)
print(expiry)


"""


"""

print("Starting")
angel_obj = fivepaise_api("avanthi")
print("Object created")
orderid = angel_obj.place_order('BANKNIFTY', 15, 'SELL', 44800, 'PE')
print(orderid)

status, price = angel_obj.get_order_status(orderid)
print(status, price)
print("Initialized")


"""




'''







#https://github.com/pinecapital/copytrade/blob/master/main.py

cred={
    "APP_NAME":"5P58919414",
    "APP_SOURCE":"11436",
    "USER_ID":"BLe4fTGVsCt",
    "PASSWORD":"u4RzW1Pies6",
    "USER_KEY":"eJrR3CM6O0ZnArmzmLgxuToloMdLQfKa",
    "ENCRYPTION_KEY":"FLXWBhW70p0i4HhAXcH6RuB41zryq0k3"
    }


TOTP = 'GU4DSMJZGQYTIXZVKBDUWRKZ'

client = FivePaisaClient(cred=cred)

totp_pin = pyotp.TOTP(TOTP).now()

client.get_totp_session('mdeeptibhat@gmail.com',totp_pin,'246800')


#print(client.positions())

req_list_ = [{"Exch": "N", "ExchType": "C", "ScripData": "ITC_EQ"},
              {"Exch": "N", "ExchType": "C", "ScripCode": "2885"}]

#print(client.fetch_market_feed_scrip(req_list_))

#print(client.place_order(OrderType='B',Exchange='N',ExchangeType='C', ScripCode = 1660, Qty=1, Price=325, AHPlaced="Y"))

print(client.order_book())


'''
