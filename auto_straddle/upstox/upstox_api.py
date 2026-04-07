"""Module providing a function for upstox"""

import traceback
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import requests
import TelegramSend
import upstox.credentials as credentials

logger = logging.getLogger(__name__)

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
        try:
            with open('upstox_access_token.txt', 'r') as f:
                self.access_token = f.read().strip()
                if self.access_token:
                    # Could add a token validation request here
                    return
        except FileNotFoundError:
            pass
            
        auth_code = getattr(credentials, 'AUTH_CODE', None)
        if not auth_code:
            logger.error("Please set AUTH_CODE in credentials or provide upstox_access_token.txt")
            print("Please set AUTH_CODE in credentials or provide upstox_access_token.txt")
            return
            
        url = f'{self.base_url}/login/authorization/token'
        headers = {
            'accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        data = {
            'code': auth_code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code',
        }
        
        try:
            response = requests.post(url, headers=headers, data=data)
            if response.status_code == 200:
                res_json = response.json()
                if 'access_token' in res_json:
                    self.access_token = res_json['access_token']
                    with open('upstox_access_token.txt', 'w') as f:
                        f.write(self.access_token)
                    logger.info("Successfully authenticated with Upstox")
                else:
                    logger.error(f"Failed to get access token: {res_json}")
            else:
                logger.error(f"Failed to authenticate: {response.text}")
        except Exception as e:
            logger.error(f"Error during authentication: {e}")

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
        
        # Map angel_one exchange strings to upstox logic
        exch_map = {'NSE': 'NSE', 'NFO': 'NFO', 'MCX': 'MCX', 'BSE': 'BSE', 'BFO': 'BFO'}
        exchange = exch_map.get(exch_seg, exch_seg)
        
        if symbol == "SENSEX":
            exchange = "BFO"

        # Note: Upstox option_type is 'PE' or 'CE'
        # Upstox strike is normally actual float without the *100 used by AngelOne, but check data if needed.
        
        # We approximate the standard filtering:
        if exch_seg == 'NSE':
            return df[(df['exchange'] == 'NSE') & (df['instrument_type'] == 'EQUITY') & (df['name'] == symbol)]
            
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
            filtered = df[(df['exchange'] == 'MCX') & (df['instrument_type'] == 'FUTCOM') & (df['name'] == symbol)]
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
