"""Module providing a function for Zerodha Kite Connect"""

# pylint: disable=W1203
# pylint: disable=W0105
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=C0209

import traceback
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import requests
from kiteconnect import KiteConnect
import pyotp
import TelegramSend
from zerodha import credentials

logger = logging.getLogger(__name__)


class zerodha_api:

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
        self.kite = KiteConnect(api_key=credentials.API_KEY)
        self.user_id = credentials.USER_ID

        attempts = 5
        while attempts > 0:
            attempts -= 1
            try:
                totp = pyotp.TOTP(credentials.TOTP_SECRET).now()

                # Generate request token via login
                request_token = self._get_request_token(totp)

                # Generate session
                data = self.kite.generate_session(request_token, api_secret=credentials.API_SECRET)
                self.kite.set_access_token(data['access_token'])
                logger.info("Successfully authenticated with Zerodha Kite")
                break
            except Exception as e:
                logger.error(f"Kite login attempt failed: {e}")
                if attempts > 0:
                    time.sleep(30)

        self.intializeSymbolTokenMap()

    def _get_request_token(self, totp):
        """Get request token via Kite login flow with TOTP."""
        import re  # pylint: disable=C0415
        from urllib.parse import urlparse, parse_qs  # pylint: disable=C0415
        session = requests.Session()

        # Step 1: GET the login page (establishes session cookies)
        login_page_url = f"https://kite.trade/connect/login?v=3&api_key={credentials.API_KEY}"
        session.get(url=login_page_url, timeout=30)

        # Step 2: POST login credentials
        login_resp = session.post(
            url="https://kite.zerodha.com/api/login",
            data={"user_id": credentials.USER_ID, "password": credentials.PASSWORD},
            timeout=30,
        )
        login_json = login_resp.json()
        if login_json.get('status') != 'success':
            raise RuntimeError(f"Kite login failed: {login_json}")

        request_id = login_json['data']['request_id']

        # Step 3: POST 2FA with TOTP
        twofa_resp = session.post(
            url="https://kite.zerodha.com/api/twofa",
            data={
                "user_id": credentials.USER_ID,
                "request_id": request_id,
                "twofa_value": totp,
            },
            timeout=30,
        )
        twofa_json = twofa_resp.json()
        if twofa_json.get('status') != 'success':
            raise RuntimeError(f"Kite 2FA failed: {twofa_json}")

        # Step 4: GET login page again — Kite redirects to redirect_uri with request_token
        # If redirect_uri (e.g. 127.0.0.1:5000) isn't running, we catch the
        # ConnectionError and extract request_token from the URL in the exception.
        try:
            response = session.get(url=login_page_url, allow_redirects=True, timeout=30)
            if "request_token" in response.url:
                return parse_qs(urlparse(response.url).query)["request_token"][0]
        except requests.exceptions.ConnectionError as e:
            match = re.findall(r"request_token=([A-Za-z0-9]+)", str(e))
            if match:
                return match[0]
        except Exception as e:
            match = re.findall(r"request_token=([A-Za-z0-9]+)", str(e))
            if match:
                return match[0]

        raise RuntimeError("Could not extract request_token from login flow")

    def intializeSymbolTokenMap(self):
        try:
            instruments = []
            for exchange in ['NSE', 'NFO', 'MCX', 'BFO']:
                try:
                    instruments.extend(self.kite.instruments(exchange))
                except Exception as e:
                    logger.warning(f"Failed to fetch instruments for {exchange}: {e}")

            self.token_df = pd.DataFrame(instruments)

            # Standardize column names to match existing pattern
            # Kite columns: instrument_token, exchange_token, tradingsymbol, name, last_price,
            #               expiry, strike, tick_size, lot_size, instrument_type, segment, exchange
            self.token_df = self.token_df.rename(columns={
                'instrument_token': 'token',
                'tradingsymbol': 'symbol',
                'lot_size': 'lotsize',
                'instrument_type': 'instrumenttype',
                'exchange': 'exch_seg',
            })

            self.token_df['expiry'] = pd.to_datetime(self.token_df['expiry'])
            self.token_df['strike'] = pd.to_numeric(self.token_df['strike'], errors='coerce')

            self.token_df.to_csv('token_map_zerodha.csv', index=False)
            logger.info(f"Kite: loaded {len(self.token_df)} instruments")
        except Exception as e:
            print(f"Error executing intializeSymbolTokenMap: {e}")
            logging.error(f"Error executing intializeSymbolTokenMap: {e}")
            try:
                self.token_df = pd.read_csv('token_map_zerodha.csv')
                self.token_df['expiry'] = pd.to_datetime(self.token_df['expiry'])
                self.token_df['strike'] = pd.to_numeric(self.token_df['strike'], errors='coerce')
                logger.info(f"Kite: loaded {len(self.token_df)} instruments from local cache")
            except Exception as e1:
                print(f"Error reading local token map: {e1}")
                logging.error(f"Error reading local token map: {e1}")
                raise e1

    def getTokenInfo(self, exch_seg, instrumenttype, symbol, strike_price, pe_ce, expiry=None):
        df = self.token_df

        # Kite uses strike as actual value (not *100 like AngelOne)
        # But we receive strike_price in the same format as AngelOne, so multiply
        strike_price_actual = strike_price * 100

        # Kite instrument types: EQ, FUT, CE, PE (not OPTIDX/OPTSTK/FUTCOM/FUTIDX)

        if symbol == "SENSEX":
            return df[(df['exch_seg'] == 'BFO') & (df['instrumenttype'] == pe_ce) &
                       (df['name'] == symbol) & (df['strike'] == strike_price_actual) &
                       (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])

        if exch_seg == 'NSE':
            eq_df = df[(df['exch_seg'] == 'NSE') & (df['instrumenttype'] == 'EQ')]
            return eq_df[eq_df['symbol'] == symbol]

        if exch_seg == 'NFO' and instrumenttype in ('FUTSTK', 'FUTIDX'):
            today = datetime.now().date()

            if expiry is not None:
                df_copy = df.copy()
                df_copy['expiry_date'] = pd.to_datetime(df_copy['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return df_copy[(df_copy['exch_seg'] == 'NFO') & (df_copy['instrumenttype'] == 'FUT') &
                               (df_copy['name'] == symbol) & (df_copy['expiry_date'] == date_obj)].sort_values(by=['expiry'])

            filtered = df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == 'FUT') &
                          (df['name'] == symbol)].sort_values(by=['expiry'])
            if filtered.empty:
                return filtered

            expiry_date = pd.to_datetime(filtered.iloc[0]['expiry']).date()
            if (expiry_date - today).days <= 10 and len(filtered) > 1:
                return filtered.iloc[1:2]
            return filtered

        if exch_seg in ['NFO', 'BFO'] and instrumenttype in ('OPTSTK', 'OPTIDX'):
            return df[(df['exch_seg'] == exch_seg) & (df['instrumenttype'] == pe_ce) &
                       (df['name'] == symbol) & (df['strike'] == strike_price_actual) &
                       (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])

        if exch_seg == 'MCX' and instrumenttype == 'FUTCOM':
            logger.info(f"Getting token info for MCX {symbol} {expiry}")
            today = datetime.now().date()

            if expiry is not None:
                df_copy = df.copy()
                df_copy['expiry_date'] = pd.to_datetime(df_copy['expiry']).dt.date
                date_obj = pd.to_datetime(expiry).date()
                return df_copy[(df_copy['exch_seg'] == 'MCX') & (df_copy['name'] == symbol) &
                               (df_copy['instrumenttype'] == 'FUT') &
                               (df_copy['expiry_date'] == date_obj)].sort_values(by=['expiry'])

            filtered = df[(df['exch_seg'] == 'MCX') & (df['instrumenttype'] == 'FUT') &
                          (df['name'] == symbol)].sort_values(by=['expiry'])
            if filtered.empty:
                return filtered

            expiry_date = pd.to_datetime(filtered.iloc[0]['expiry']).date()
            if (expiry_date - today).days <= 10 and len(filtered) > 1:
                return filtered.iloc[1:2]
            return filtered

        return None

    def get_best_price(self, symbol, token, exchange, buy_sell):  # pylint: disable=W0613
        """Fetch best price for limit orders using Kite market depth, falling back to LTP.

        For BUY returns best ask; for SELL returns best bid.
        Falls back to LTP +/- 0.5% buffer if depth is unavailable.
        """
        try:
            instrument_key = f"{exchange}:{symbol}"
            quote = self.kite.quote([instrument_key])

            if instrument_key in quote:
                q = quote[instrument_key]
                depth = q.get('depth', {})
                if buy_sell == 'BUY':
                    asks = depth.get('sell', [])
                    for ask in asks:
                        p = float(ask.get('price', 0))
                        if p > 0:
                            logger.info(f"Kite market depth ask price for {instrument_key}: {p}")
                            return p
                else:
                    bids = depth.get('buy', [])
                    for bid in bids:
                        p = float(bid.get('price', 0))
                        if p > 0:
                            logger.info(f"Kite market depth bid price for {instrument_key}: {p}")
                            return p

                # Fallback to LTP
                ltp = float(q.get('last_price', 0))
                if ltp > 0:
                    if buy_sell == 'BUY':
                        price = round(ltp * 1.005, 2)
                    else:
                        price = round(ltp * 0.995, 2)
                    logger.info(f"Kite LTP fallback price for {instrument_key}: {price} (ltp={ltp})")
                    return price

            logger.error(f"Kite: no price available for {instrument_key}")
        except Exception as e:
            logger.error(f"Kite get_best_price failed for {symbol}: {e}")

        return 0

    def _find_recent_order(self, tradingsymbol, transactiontype, qty, producttype=None):
        """Check order book for a non-rejected order matching the given params.

        Used after a timeout to detect if the server processed the order before
        the client gave up, to avoid placing a duplicate on retry.
        """
        try:
            orders = self.kite.orders()
            if not orders:
                return None
            for o in orders:
                if o.get('status', '') in ('REJECTED', 'CANCELLED'):
                    continue
                if (str(o.get('tradingsymbol', '')) == str(tradingsymbol)
                        and str(o.get('transaction_type', '')) == str(transactiontype)
                        and int(o.get('quantity', 0)) == int(qty)):
                    if producttype is not None and str(o.get('product', '')) != str(producttype):
                        continue
                    return o.get('order_id')
        except Exception as e:
            logger.error(f"Error checking order book for recent order: {e}")
        return None

    def place_order_cash(self, symbol, qty, buy_sell):
        try:
            print(f"Placing order for symbol: {symbol}, qty: {qty}, buy_sell: {buy_sell}")
            tokenInfo = self.getTokenInfo('NSE', 'EQ', symbol, 0, 'X')
            if tokenInfo is None or tokenInfo.empty:
                return -1

            tradingsymbol = tokenInfo.iloc[0]['symbol']

            orderid = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=tradingsymbol,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY if buy_sell == 'BUY' else self.kite.TRANSACTION_TYPE_SELL,
                quantity=int(qty),
                product=self.kite.PRODUCT_CNC,
                order_type=self.kite.ORDER_TYPE_MARKET,
            )
            return orderid
        except Exception as e:
            print(f"Order placement failed: {str(e)}")
            logger.error(f"Order placement failed: {str(e)}")
            return -1

    def place_order(self, symbol, qty, buy_sell, strike_price, pe_ce, intraday=True):

        product = self.kite.PRODUCT_MIS if intraday else self.kite.PRODUCT_NRML

        try:
            df = self.getTokenInfo('NFO', 'OPTIDX', symbol, strike_price, pe_ce)
            if df is None or df.empty:
                return -1

            try:
                if pd.to_datetime(df.iloc[0]['expiry']).date() < datetime.now().date():
                    if len(df) > 1:
                        tokenInfo = df.iloc[1]
                    else:
                        tokenInfo = df.iloc[0]
                else:
                    tokenInfo = df.iloc[0]
            except Exception as e:
                print(f"Error executing place_order: {e}")
                logging.error(f"Error executing place_order: {e}")
                tokenInfo = df.iloc[0]

            if symbol == "SENSEX":
                exchange = self.kite.EXCHANGE_BFO
            else:
                exchange = self.kite.EXCHANGE_NFO

            tradingsymbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])

            if qty % lot != 0:
                return -1

            transaction_type = self.kite.TRANSACTION_TYPE_BUY if buy_sell == 'BUY' else self.kite.TRANSACTION_TYPE_SELL

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {tradingsymbol}, Token: {token}, Lot: {lot} exchange: {exchange}")
            try:
                try:
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=exchange,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=product,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(5)
                    existing_orderid = self._find_recent_order(tradingsymbol, buy_sell, qty, product)
                    if existing_orderid:
                        logger.info(f"Order {existing_orderid} already exists after timeout, skipping retry")
                        orderid = existing_orderid
                    else:
                        try:
                            orderid = self.kite.place_order(
                                variety=self.kite.VARIETY_REGULAR,
                                exchange=exchange,
                                tradingsymbol=tradingsymbol,
                                transaction_type=transaction_type,
                                quantity=qty,
                                product=product,
                                order_type=self.kite.ORDER_TYPE_MARKET,
                            )
                        except Exception as e2:
                            print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                            print(f"Error executing place_order after timeout: {e2}")
                            logger.error(f"Error executing place_order after timeout: {e2}")
                            return -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    logger.error(f"Error executing place_order again: {e}")
                    x = TelegramSend.telegram_send_api()
                    x.send_message("-4008545231", f"Warning kite {tradingsymbol} order Pls check")
                    time.sleep(2)
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=exchange,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=product,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                except Exception as e1:
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1

            if orderid is None or orderid == '' or orderid == 0:
                logger.error(f"Kite API returned invalid order ID: {orderid} for {tradingsymbol}")
                print(f"Kite API returned invalid order ID: {orderid}")
                return -1

            return orderid
        except Exception as e:
            logger.error(f"Error executing place_order: {e}")
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order: {e}")
            return -1

    def place_order_commodity(self, symbol, qty, buy_sell, expiry=None, iscommodity=True):
        logger.info(f"Placing commodity order for {symbol}, qty: {qty}, type: {buy_sell}, expiry: {expiry}, iscommodity: {iscommodity}")
        original_symbol = symbol
        try:
            mapped = self.SYMBOL_PREFIX_MAP.get(symbol.upper(), symbol)
            symbol = mapped

            if iscommodity:
                tokenInfo = self.getTokenInfo('MCX', 'FUTCOM', symbol, 0, 'X', expiry)
            else:
                tokenInfo = self.getTokenInfo('NFO', 'FUTIDX', symbol, 0, 'X', expiry)

            if tokenInfo is None or tokenInfo.empty:
                logger.error(f"place_order_commodity: no token found for {symbol} expiry={expiry}")
                return -1, -1

            t_info = tokenInfo.iloc[0]
            tradingsymbol = t_info['symbol']
            token = t_info['token']
            lot = int(t_info['lotsize'])
            logger.info(f"Token info: symbol={tradingsymbol}, token={token}, lot={lot}")
            logger.info(f"Expiry date: {t_info['expiry']}")

            total_qty = qty * lot

            if iscommodity:
                exchange = self.kite.EXCHANGE_MCX
            else:
                exchange = self.kite.EXCHANGE_NFO

            transaction_type = self.kite.TRANSACTION_TYPE_BUY if buy_sell == 'BUY' else self.kite.TRANSACTION_TYPE_SELL

            # Use MARKET order (Kite free accounts don't have market depth/LTP access)
            # Kite applies automatic market protection percentage for MCX MARKET orders
            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {tradingsymbol}, Token: {token}, Lot: {lot}")
            logger.info(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {tradingsymbol}, Token: {token}, Lot: {lot}")

            orderid = None
            try:
                try:
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=exchange,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=total_qty,
                        product=self.kite.PRODUCT_NRML,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(5)
                    existing_orderid = self._find_recent_order(tradingsymbol, buy_sell, total_qty, self.kite.PRODUCT_NRML)
                    if existing_orderid:
                        logger.info(f"Order {existing_orderid} already exists after timeout, skipping retry")
                        orderid = existing_orderid
                    else:
                        try:
                            orderid = self.kite.place_order(
                                variety=self.kite.VARIETY_REGULAR,
                                exchange=exchange,
                                tradingsymbol=tradingsymbol,
                                transaction_type=transaction_type,
                                quantity=total_qty,
                                product=self.kite.PRODUCT_NRML,
                                order_type=self.kite.ORDER_TYPE_MARKET,
                            )
                        except Exception as e2:
                            print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                            print(f"Error executing place_order after timeout: {e2}")
                            logger.error(f"Error executing place_order after timeout: {e2}")
                            return -1, -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    logger.error(f"Error executing place_order again: {e}")
                    print(f"Error: {e}")
                    x = TelegramSend.telegram_send_api()
                    x.send_message("-4008545231", f"Warning kite {tradingsymbol} order Pls check")
                    time.sleep(2)

                    trade_type = 'long' if buy_sell == 'BUY' else 'short'
                    pos_type, _ = self.get_commodity_position(original_symbol, trade_type)
                    if pos_type is not None:
                        logger.info(f"Position already exists for {original_symbol} ({trade_type}) after exception. Skipping retry to avoid duplicate.")
                        return -1, t_info['expiry']

                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=exchange,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=total_qty,
                        product=self.kite.PRODUCT_NRML,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                except Exception as e1:
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(f"Error executing place_order: {e1}")
                    logging.error(f"Error executing place_order: {e1}")
                    return -1, -1

            if orderid is None or orderid == '' or orderid == 0:
                logger.error(f"Kite API returned invalid order ID: {orderid} for commodity {tradingsymbol}")
                print(f"Kite API returned invalid order ID: {orderid}")
                return -1, -1

            return orderid, t_info['expiry']
        except Exception as e:
            logging.error(f"Error executing place_order_commodity: {e}")
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order_commodity: {e}")
            return -1, -1

    def place_order_option_buy(self, symbol, qty, buy_sell, strike_price, pe_ce):
        try:
            df = self.token_df
            strike_price_actual = strike_price * 100
            filtered = df[(df['exch_seg'] == 'NFO') & (df['instrumenttype'] == pe_ce) &
                          (df['name'] == symbol) & (df['strike'] == strike_price_actual) &
                          (df['symbol'].str.endswith(pe_ce))].sort_values(by=['expiry'])

            if filtered.empty:
                print("Token info not found")
                return -1

            try:
                today = datetime.now().date()
                next_month = today.replace(day=28) + timedelta(days=4)
                last_day = next_month - timedelta(days=next_month.day)

                filtered = filtered.copy()
                filtered['expiry_date'] = pd.to_datetime(filtered['expiry']).dt.date
                current_month_expiries = filtered[filtered['expiry_date'] <= last_day]

                if not current_month_expiries.empty:
                    tokenInfo = current_month_expiries.iloc[-1]
                else:
                    next_month_expiries = filtered[filtered['expiry_date'] > last_day]
                    if not next_month_expiries.empty:
                        tokenInfo = next_month_expiries.iloc[-1]
                    else:
                        tokenInfo = filtered.iloc[-1]
            except Exception as e:
                logging.error(f"Error executing place_order: {e}")
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                tokenInfo = filtered.iloc[-1]

            tradingsymbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])

            if qty % lot != 0:
                return -1

            transaction_type = self.kite.TRANSACTION_TYPE_BUY if buy_sell == 'BUY' else self.kite.TRANSACTION_TYPE_SELL

            print(f" Time: {datetime.now().strftime('%H:%M:%S')} Symbol: {tradingsymbol}, Token: {token}, Lot: {lot}")
            try:
                try:
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=self.kite.EXCHANGE_NFO,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=self.kite.PRODUCT_NRML,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                    print(f" After order Time: {datetime.now().strftime('%H:%M:%S')})")
                except requests.exceptions.Timeout:
                    print("Order placement timed out, retrying once")
                    logger.warning("Order placement timed out, retrying once")
                    time.sleep(5)
                    existing_orderid = self._find_recent_order(tradingsymbol, buy_sell, qty, self.kite.PRODUCT_NRML)
                    if existing_orderid:
                        logger.info(f"Order {existing_orderid} already exists after timeout, skipping retry")
                        orderid = existing_orderid
                    else:
                        try:
                            orderid = self.kite.place_order(
                                variety=self.kite.VARIETY_REGULAR,
                                exchange=self.kite.EXCHANGE_NFO,
                                tradingsymbol=tradingsymbol,
                                transaction_type=transaction_type,
                                quantity=qty,
                                product=self.kite.PRODUCT_NRML,
                                order_type=self.kite.ORDER_TYPE_MARKET,
                            )
                        except Exception as e2:
                            print(''.join(traceback.format_exception(type(e2), e2, e2.__traceback__)))
                            logger.error(f"Error executing place_order after timeout: {e2}")
                            return -1
            except Exception as e:
                try:
                    print("Error placing order, trying again")
                    print(f"Error: {e}")
                    logger.error(f"Error executing place_order again: {e}")
                    x = TelegramSend.telegram_send_api()
                    x.send_message("-4008545231", f"Warning kite {tradingsymbol} option buy order Pls check")
                    time.sleep(2)
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=self.kite.EXCHANGE_NFO,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=self.kite.PRODUCT_NRML,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )
                except Exception as e1:
                    logging.error(f"Error executing place_order: {e1}")
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    return -1

            if orderid is None or orderid == '' or orderid == 0:
                logger.error(f"Kite API returned invalid order ID: {orderid} for option buy {tradingsymbol}")
                print(f"Kite API returned invalid order ID: {orderid}")
                return -1

            return orderid
        except Exception as e:
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing place_order: {e}")
            logger.error(f"Error executing place_order: {e}")
            return -1

    def place_order_synthetic_future(self, symbol, qty, buy_sell, strike_price, pe_ce, expiry=None):
        """Place an order for synthetic futures.
        Returns: (order_id, expiry_date) tuple or (-1, None) on failure
        """
        try:
            df = self.getTokenInfo("NFO", "OPTIDX", symbol, strike_price, pe_ce, expiry)

            if df is None or (isinstance(df, int) and df == -1) or df.empty:
                logger.error(f"No valid contracts found for {symbol} {strike_price} {pe_ce}")
                return -1, None

            tokenInfo = None

            df = df.copy()
            df['expiry_date'] = pd.to_datetime(df['expiry']).dt.date
            today = datetime.now().date()

            if expiry is not None:
                try:
                    expiry_date = pd.to_datetime(expiry).date()
                    matching_expiries = df[df['expiry_date'] == expiry_date]

                    if matching_expiries.empty:
                        logger.error(f"No contract found for specified expiry {expiry_date}")
                        return -1, None

                    tokenInfo = matching_expiries.iloc[0]
                except Exception as e:
                    logger.error(f"Error parsing expiry date {expiry}: {e}")
                    return -1, None
            else:
                future_expiries = df[df['expiry_date'] > (today + timedelta(days=8))]

                if future_expiries.empty:
                    logger.error(f"No valid future expiries found for {symbol}")
                    return -1, None

                tokenInfo = future_expiries.iloc[0]

            tradingsymbol = tokenInfo['symbol']
            token = tokenInfo['token']
            lot = int(tokenInfo['lotsize'])

            logger.info(f"Selected contract: {tradingsymbol}, token: {token}, expiry: {tokenInfo['expiry_date']}")
            print(f"Selected contract: {tradingsymbol}, token: {token}, expiry: {tokenInfo['expiry_date']}")

            if qty % lot != 0:
                logger.error(f"Quantity {qty} not multiple of lot size {lot}")
                print(f"Quantity {qty} not multiple of lot size {lot}")
                return -1, None

            transaction_type = self.kite.TRANSACTION_TYPE_BUY if buy_sell == 'BUY' else self.kite.TRANSACTION_TYPE_SELL

            for attempt in range(2):
                try:
                    orderid = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=self.kite.EXCHANGE_NFO,
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=qty,
                        product=self.kite.PRODUCT_NRML,
                        order_type=self.kite.ORDER_TYPE_MARKET,
                    )

                    if orderid is None or orderid == '' or orderid == 0:
                        logger.error(f"Kite API returned invalid order ID: {orderid} for synthetic future {tradingsymbol}")
                        print(f"Kite API returned invalid order ID: {orderid}")
                        continue

                    logger.info(f"Order placed successfully: {orderid}")
                    return orderid, tokenInfo['expiry_date']
                except requests.exceptions.Timeout:
                    logger.warning(f"Order placement timeout (attempt {attempt+1})")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Order placement error (attempt {attempt+1}): {e}")
                    if attempt == 0:
                        time.sleep(2)
                        try:
                            TelegramSend.telegram_send_api().send_message(
                                "-4008545231",
                                f"Warning kite {tradingsymbol} order failed: {str(e)[:100]}")
                        except Exception as telegram_error:
                            logger.debug(f"Failed to send Telegram alert: {telegram_error}")

            return -1, None

        except Exception as e:
            logger.error(f"Fatal error in place_order_synthetic_future: {e}")
            traceback.print_exc()
            return -1, None

    def get_order_status(self, order_id):
        try:
            if pd.isna(order_id) or order_id is None or order_id == -1 or str(order_id).lower() == 'nan':
                logger.error(f"Invalid order_id received for status check: {order_id}")
                return "NotFound", -1

            try:
                order_id = str(int(float(order_id)))
            except (ValueError, TypeError):
                logger.error(f"Could not convert order_id {order_id} to integer")
                return "NotFound", -1

            try:
                order_history = self.kite.order_history(order_id)
            except Exception as e:
                try:
                    print("Error getting order history, trying again")
                    print(f"Error: {e}")
                    time.sleep(2)
                    order_history = self.kite.order_history(order_id)
                except Exception as e1:
                    print(f"Error: {e1}")
                    return -1, -1

            if not order_history:
                return "NotFound", -1

            # Last entry in order_history has the latest status
            latest = order_history[-1]
            order_status = latest.get('status', '').upper()
            average_price = float(latest.get('average_price', 0) or 0)

            logger.info(f"Kite raw order status for {order_id}: '{order_status}'")

            if order_status == 'COMPLETE':
                order_ret = "Complete"
            elif order_status in ('OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'):
                order_ret = "Open"
            elif order_status == 'REJECTED':
                order_ret = "Rejected"
            elif order_status == 'CANCELLED':
                order_ret = "Cancelled"
            else:
                logger.warning(f"Unknown Kite order status '{order_status}' for order {order_id}, treating as Open")
                order_ret = "Open"

            print("Order Status", order_ret)
            return order_ret, average_price
        except Exception as e:
            print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
            print(f"Error executing get_order_status: {e}")
            logger.error(f"Error executing get_order_status: {e}")
            return -1, -1

    def get_commodity_position(self, symbol, trade_type):
        """Check if there is an open MCX commodity position matching the intended trade.

        Args:
            symbol: Commodity display name ('GOLD', 'SILVER', 'COPPER', etc.)
            trade_type: 'long' or 'short'

        Returns:
            (trade_type, avg_price) if a matching position is found, (None, 0) otherwise
        """
        try:
            positions = self.kite.positions()
            if not positions:
                logger.warning(f"No position data returned from Kite for {symbol}")
                return None, 0

            mcx_prefix = self.SYMBOL_PREFIX_MAP.get(symbol.upper(), symbol.upper())

            # Check both day and net positions
            all_positions = positions.get('net', []) + positions.get('day', [])

            for pos in all_positions:
                if pos.get('exchange', '') != 'MCX':
                    continue
                tradingsymbol = pos.get('tradingsymbol', '').upper()
                if not tradingsymbol.startswith(mcx_prefix.upper()):
                    continue

                net_qty = int(pos.get('quantity', 0))
                if net_qty > 0 and trade_type == 'long':
                    avg_price = float(pos.get('average_price', 0) or 0)
                    logger.info(f"Kite: Found LONG position for {symbol}: qty={net_qty} avg={avg_price}")
                    return 'long', avg_price
                if net_qty < 0 and trade_type == 'short':
                    avg_price = float(pos.get('average_price', 0) or 0)
                    logger.info(f"Kite: Found SHORT position for {symbol}: qty={net_qty} avg={avg_price}")
                    return 'short', avg_price

            logger.info(f"Kite: No matching {trade_type} position found for {symbol}")
            return None, 0
        except Exception as e:
            logger.error(f"Error checking Kite commodity position for {symbol}: {e}")
            return None, 0

    def get_ledger_balance(self):
        """Fetch the ledger balance for the account"""
        try:
            margins = self.kite.margins()
            equity_available = float(margins.get('equity', {}).get('available', {}).get('live_balance', 0) or 0)
            commodity_available = float(margins.get('commodity', {}).get('available', {}).get('live_balance', 0) or 0)
            balance = equity_available + commodity_available
            logger.info(f"Kite Ledger balance: equity={equity_available} commodity={commodity_available} total={balance}")
            return balance
        except Exception as e:
            logger.error(f"Error fetching Kite ledger balance: {e}")
            return 0.0
