"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105


from datetime import datetime
import os
import traceback
import logging
import requests
#from PlaceOrder import PlaceOrder
import pandas as pd
from nsetools import Nse
import TelegramSend
import configuration
from exchange_state import ExchangeData
import brokrage_calculator


headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en-US,en;q=0.9,en-IN;q=0.8,en-GB;q=0.7",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        }

#from OptionChainData import OptionChainData
#from pathlib import Path
#from PlaceOrder import PlaceOrder

# Set up logging
logger = logging.getLogger(__name__)

class cash_stratergy:
    def __init__(self):
        self.csv_path = "cash_stratergy.csv"
        self.remote_csv_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSmdwtCAt2oAYnuJGBb3zp7L0Q-iYSZoCMLvy3cfLrz48kp9cHvBqPjRp_p7uRc0Muw_lE7kl0wOnNP/pub?output=csv"
        self.correct_rejected_orders_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTpaSDfm5rZ8LbTKKA4hnKw7qtTR70epicX2g5u9CfkfDtzyW9pgNJ79icW0yumKQ3z7vnzJlcrcTpb/pub?output=csv"
        self.execution_tracker = {"morning": 0, "afternoon": 0}
        self.nso_open = None
        self._cached_positions = None
        self._last_fetch_time = None


    def nsefetch(self, payload):
        try:
            output = requests.get(payload,headers=headers, timeout=10).json()
            #print(output)
        except ValueError:
            s =requests.Session()
            output = s.get("http://nseindia.com",headers=headers)
            output = s.get(payload,headers=headers).json()
        return output

    def nse_custom_function_secfno(self, symbol,attribute="lastPrice"):
        current_time = datetime.now()
        print("Fetching data from NSE" + symbol)
        try:
            if not hasattr(self, '_last_fetch_time') or not hasattr(self, '_cached_positions') or \
                self._last_fetch_time is None or (current_time - self._last_fetch_time).total_seconds() > 300:
                positions = self.nsefetch('https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O')
                self._cached_positions = positions
                self._last_fetch_time = current_time
            else:
                positions = self._cached_positions
            endp = len(positions['data'])
            for x in range(0, endp):
                if positions['data'][x]['symbol']==symbol.upper():
                    value = float(positions['data'][x][attribute])
                    return value
        except Exception as e:
            print("Error fetching data from NSE")
            print(e)

    def correct_rejected_orders(self):
        """
        Corrects rejected orders by re-executing them based on remote CSV data.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Download and validate remote CSV
            remote_data = pd.read_csv(self.correct_rejected_orders_url)
            required_columns = ['sl_no', 'leg', 'account', 'symbol']
            if not all(col in remote_data.columns for col in required_columns):
                logger.error("Remote CSV missing required columns")
                return False

            # Load or create local CSV
            try:
                local_data = pd.read_csv(self.csv_path)
            except FileNotFoundError:
                logger.warning("Local CSV not found. Creating new file")
                local_data = pd.DataFrame(columns=remote_data.columns)
                local_data.to_csv(self.csv_path, index=False)
                return True

            # Process each row in remote data
            for _, row in remote_data.iterrows():
                try:
                    local_row = local_data[local_data['sl_no'] == row['sl_no']]
                    if local_row.empty:
                        logger.warning(f"Row {row['sl_no']} not found in local CSV")
                        continue

                    if row['leg'] == 'open':
                        self._handle_open_correction(local_data, row)
                    elif row['leg'] == 'close':
                        self._handle_close_correction(local_data, row)
                    else:
                        logger.warning(f"Invalid leg value: {row['leg']} for sl_no {row['sl_no']}")

                except Exception as e:
                    logger.error(f"Error processing row {row['sl_no']}: {str(e)}")
                    continue

            # Save updates
            local_data.to_csv(self.csv_path, index=False)
            logger.info("Rejected orders corrected successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to correct rejected orders: {str(e)}")
            return False

    def _handle_open_correction(self, local_data, row):
        """Handle open leg corrections"""
        logger.info(f"Correcting open order for row {row['sl_no']}")
        mask = local_data['sl_no'] == row['sl_no']
        local_data.loc[mask, 'status'] = 'open'
        local_data.loc[mask, 'open_order_status'] = 'Complete'
        local_data.loc[mask, 'buy_order_id'] = None
        local_data.loc[mask, 'buy_price'] = row['price']
        local_data.loc[mask, 'open_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _handle_close_correction(self, local_data, row):
        """Handle close leg corrections"""
        try:
            logger.info(f"Correcting close order for row {row['sl_no']}")
            mask = local_data['sl_no'] == row['sl_no']
            local_data.loc[mask, 'status'] = 'close'
            local_data.loc[mask, 'close_order_status'] = 'Complete'
            local_data.loc[mask, 'close_order_id'] = None
            local_data.loc[mask, 'sell_price'] = row['price']
            local_data.loc[mask, 'close_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self._calculate_and_record_pnl(local_data.loc[mask].iloc[0], row)

        except Exception as e:
            logger.error(f"Error in close correction: {str(e)}")
            raise

    def _calculate_and_record_pnl(self, local_row, remote_row):
        """Calculate and record P&L for closed trades"""
        try:
            # Ensure numeric values
            buy_price = float(local_row['buy_price'])
            quantity = float(local_row['quantity'])
            sell_price = float(remote_row['price'])

            profit_loss = (sell_price - buy_price) * quantity

            # Calculate brokerage
            brokerage_dict = brokrage_calculator.calculate_equity_delivery(
                buy_price, sell_price, quantity)
            brokerage = brokerage_dict['total_charges']

            # Record PnL to CSV file
            pl_dict = {
                'Date': datetime.now().strftime("%Y-%m-%d"),
                'Account': remote_row['account'],
                'Symbol': remote_row['symbol'],
                'Quantity': quantity,
                'NumberofTrade': 1,
                'TotalPNL': profit_loss,
                'Brokarge': brokerage,
                'CloseTime': datetime.now().strftime("%H:%M:%S"),
                'Stratergy': 'cash_short',
                'NetPNL': profit_loss - brokerage
            }

            current_month = datetime.now().strftime("%m")
            file_name = f"pnl/consolidated_pnl_{current_month}.csv"
            if os.path.exists(file_name):
                df = pd.read_csv(file_name)
                df = pd.concat([df, pd.DataFrame([pl_dict])], ignore_index=True)
                df.to_csv(file_name, index=False)
            else:
                df = pd.DataFrame([pl_dict])
                df.to_csv(file_name, index=False)

            # Send notification to Telegram
            telegram_group = remote_row['account'] + "_telegram"
            id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
            x = TelegramSend.telegram_send_api()
            x.send_message(id1, f"Cash strategy P/L {remote_row['account']} {remote_row['symbol']} {profit_loss}")

        except Exception as e:
            print(''.join(traceback.format_exception(e)))
            logger.error(f"Error calculating PnL: {str(e)}")
            raise

    def sync_cash_strategy(self):
        """
        Syncs the remote CSV with the local CSV based on today's date and updates
        or inserts rows accordingly.
        """
        # Step 1: Download the CSV from the remote URL
        remote_data = pd.read_csv(self.remote_csv_url)
        remote_data['date'] = pd.to_datetime(remote_data['date'], errors='coerce')

        # Step 2: Load the local CSV
        try:
            local_data = pd.read_csv(self.csv_path)
        except FileNotFoundError:
            print("Local CSV not found. Creating a new one.")
            local_data = pd.DataFrame(columns=remote_data.columns)

        # Step 3: Filter rows for today's date
        today_date = datetime.now().date()
        today_rows = remote_data[remote_data['date'].dt.date == today_date]

        # Step 4: Sync rows
        for _, row in today_rows.iterrows():
            sl_no = row['sl_no']

            if sl_no in local_data['sl_no'].values:
                # Update existing entry
                local_data.loc[local_data['sl_no'] == sl_no, ['sl', 'profit_target']] = row[['sl', 'profit_target']].values
                print(f"Updated entry for sl_no: {sl_no}")
            else:
                # Add new entry
                local_data = pd.concat([local_data, pd.DataFrame([row])], ignore_index=True)
                print(f"Added new entry for sl_no: {sl_no}")

        # Save the updated local CSV
        local_data.to_csv(self.csv_path, index=False)
        print("Sync completed successfully.")

    def execute_strategy(self, place_order, max_executions=2):
        """
        Executes the cash strategy based on the CSV file and strategy rules.
        
        Args:
            place_order (PlaceOrder): Instance of PlaceOrder to handle orders.
            max_executions (int): Maximum number of executions allowed in the morning and afternoon.
        """

        # return if time is less than 9:15
        now = datetime.now()
        if datetime.strptime("09:15:00", "%H:%M:%S").time() > now.time():
            return

        # Check NFO market is open or not
        if self.nso_open is None:
            exchange_data = ExchangeData()
            exchange_data_var = exchange_data.is_nfo_open()
            if exchange_data_var is False:
                print("NFO market is closed")
                self.nso_open = False
                return
            else:
                self.nso_open = True

        elif self.nso_open is False:
            return

        # Determine if the function can execute based on the time of day
        now = datetime.now()
        if datetime.strptime("09:27:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("09:33:00", "%H:%M:%S").time():
            logger.info(f"Execution tracker morning count: {self.execution_tracker['morning']}")
            print(f"Execution tracker morning count: {self.execution_tracker['morning']}")
            # Check morning executions limit:
            if self.execution_tracker["morning"] >= max_executions:
                logger.info("Maximum exceeded.")
                return
            self.execution_tracker["morning"] += 1
        elif datetime.strptime("15:15:00", "%H:%M:%S").time() <= now.time() <= datetime.strptime("15:20:00", "%H:%M:%S").time():
            logger.info(f"Execution tracker evening count: {self.execution_tracker['afternoon']}")
            # Check afternoon executions limit:
            if self.execution_tracker["afternoon"] >= max_executions + 1:
                logger.info("Maximum exceeded.")
                return

            if self.execution_tracker["afternoon"] == max_executions:
                self.send_csv()

            self.execution_tracker["afternoon"] += 1
        else:
            return

        self.correct_rejected_orders()

        # Load the CSV
        logger.info("Executing cash strategy.")
        data = pd.read_csv(self.csv_path)

        # Step 2.4: Process rows with status 'new'
        for idx, row in data[data['status'] == 'new'].iterrows():
            logger.info(f"Processing row {row['sl_no']} with symbol {row['symbol']} and price {row['sl']}")
            try:
                symbol = row['symbol']
                nse = Nse()
                last_price = nse.get_quote(symbol)['lastPrice']
            except Exception as e:
                print(f"Error fetching price for symbol {row['symbol']}: {e}. Ensure the symbol is correct for NSE.")
                continue

            try:
                if last_price > row['sl']:
                    print(f"Processing row {row['sl_no']} with symbol {row['symbol']} and price {last_price}")
                    logger.info(f"Processing row {row['sl_no']} with symbol {row['symbol']} and price {last_price}")
                    quantity = int(row['amount'] / last_price)
                    order_id = place_order.place_cash_order(row['account'], row['symbol'], quantity, "BUY")

                    # Update the row in the DataFrame
                    data.loc[idx, 'buy_order_id'] = order_id
                    data.loc[idx, 'buy_price'] = last_price
                    data.loc[idx, 'open_order_status'] = 'open_pending'
                    data.loc[idx, 'status'] = 'open_pending'
                    data.loc[idx, 'quantity'] = quantity
                else:
                    print(f"Skipping row {row['sl_no']} with symbol {row['symbol']} and price {last_price}")
                    data.loc[idx, 'open_order_status'] = 'rejected'
                    data.loc[idx, 'status'] = 'rejected'

            except Exception as e:
                print(''.join(traceback.format_exception(e)))
                data.loc[idx, 'open_order_status'] = 'rejected'
                data.loc[idx, 'status'] = 'rejected'
                print(f"Error processing 'new' row {row['sl_no']}: {e}")
                logger.error(f"Error processing 'new' row {row['sl_no']}: {e}")
                telegram_group = row['account'] + "_telegram"
                id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                x = TelegramSend.telegram_send_api()
                # Send error over telegramsend send_message
                x.send_message(id1, f"Cash startergy open error {row['account']} {symbol}")

        # Step 2.5: Process rows with status 'open'
        for idx, row in data[data['status'] == 'open'].iterrows():
            try:
                symbol = row['symbol']
                logger.info(f"Processing row {row['sl_no']} with symbol {row['symbol']} and price {row['sl']}")
                nse = Nse()
                last_price = nse.get_quote(symbol)['lastPrice']
                if last_price <= row['sl'] or last_price >= row['profit_target']:
                    if row['account'] == "deepti":
                        order_id = place_order.place_cash_order(row['account'], row['symbol'], row['quantity'], "SELL")

                        # Update the row in the DataFrame
                        data.loc[idx, 'close_order_id'] = order_id
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                        data.loc[idx, 'status'] = 'close_pending'
                    else:
                        if row['account'] == "sharekhan" or row['account'] == "anvitha" or row['account'] == "adithya":
                            telegram_group = "deepti" + "_telegram"
                        else:
                            telegram_group = row['account'] + "_telegram"
                        data.loc[idx, 'close_order_status'] = 'close_pending'
                        data.loc[idx, 'status'] = 'close_pending'
                        id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                        x = TelegramSend.telegram_send_api()

                        # Send error over telegramsend send_message
                        x.send_message(id1, f"Cash startergy please close  {row['account']} {row['symbol']}")
            except Exception as e:
                print(''.join(traceback.format_exception(e)))
                print(f"Error processing 'open' row {row['sl_no']}: {e}")
                data.loc[idx, 'open_order_status'] = 'rejected'
                data.loc[idx, 'status'] = 'rejected'
                telegram_group = row['account'] + "_telegram"
                id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                x = TelegramSend.telegram_send_api()
                # Send error over telegramsend send_message
                x.send_message(id1, f"Cash startergy close error {row['account']} {row['symbol']}")

        # Step 2.6: Process rows with status 'open_pending' or 'close_pending'
        for idx, row in data[data['status'].isin(['open_pending', 'close_pending'])].iterrows():
            try:
                logger.info(f"Processing row {row['sl_no']} with symbol {row['symbol']} and price {row['sl']}")
                order_id = row['buy_order_id'] if row['status'] == 'open_pending' else row['close_order_id']
                status, final_price = place_order.order_status(row['account'], order_id, row['buy_price'])

                if status == "Complete":
                    if row['status'] == 'close_pending':
                        # Calulate profilr/loss
                        data.loc[idx, 'sell_price'] = final_price
                        data.loc[idx, 'close_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        profit_loss = (final_price - row['buy_price']) * row['quantity']

                        telegram_group = row['account'] + "_telegram"
                        id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                        x = TelegramSend.telegram_send_api()
                        # Send error over telegramsend send_message
                        x.send_message(id1, f"Cash startergy p/l {row['account']} {row['symbol']} {row['strategy']} {profit_loss}")

                        brokarage_dict = brokrage_calculator.calculate_equity_delivery(row['buy_price']\
                                                                                , row['sell_price'],\
                                                                             row['quantity'])
                        brokrage = brokarage_dict['total_charges']

                        pl_dict = {
                            'Date': datetime.now().strftime("%Y-%m-%d"),
                            'Account': row['account'],
                            'Symbol': row['symbol'],
                            'Quantity': quantity,
                            'NumberofTrade': 1,
                            'TotalPNL': profit_loss * 1,
                            'Brokarge': brokrage,
                            'CloseTime': datetime.now().strftime("%H:%M:%S"),
                            'Stratergy': 'cash_short',
                            'NetPNL': profit_loss - brokrage
                        }

                        current_month = datetime.now().strftime("%m")
                        file_name = f"pnl/consolidated_pnl_{current_month}.csv"
                        if os.path.exists(file_name):
                            df = pd.read_csv(file_name)
                            df = pd.concat([df, pd.DataFrame([pl_dict])], ignore_index=True)
                            df.to_csv(file_name, index=False)
                        else:
                            df = pd.DataFrame([pl_dict])
                            df.to_csv(file_name, index=False)

                    if row['status'] == 'open_pending':
                        data.loc[idx, 'buy_price'] = final_price
                        data.loc[idx, 'open_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    data.loc[idx, 'status'] = 'open' if row['status'] == 'open_pending' else 'close'
                    data.loc[idx, 'open_order_status' if row['status'] == 'open_pending' else 'close_order_status'] = 'Complete'
            except Exception as e:
                print(f"Error processing 'pending' row {row['sl_no']}: {e}")
                data.loc[idx, 'open_order_status'] = 'rejected'
                data.loc[idx, 'status'] = 'rejected'
                telegram_group = row['account'] + "_telegram"
                id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
                x = TelegramSend.telegram_send_api()
                # Send error over telegramsend send_message
                x.send_message(id1, f"Cash startergy pending error {row['account']} {row['symbol']}")

        # Save the updated CSV
        data.to_csv(self.csv_path, index=False)
        print("Strategy executed and CSV updated.")

    # Function to send csv file over telegram
    def send_csv(self):
        logger.info("Sending CSV file over Telegram.")
        telegram_group = "dummy" + "_telegram"
        id1 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)
        x = TelegramSend.telegram_send_api()
        # Send error over telegramsend send_message
        x.send_file(id1, self.csv_path)


"""
# Example usage:
if __name__ == "__main__":

    from PlaceOrder import PlaceOrder

    accounts = ["dummy", "deepti"]
    #accounts = ["dummy"]
    symbols = ["BANKNIFTY"]

    place_order = PlaceOrder()

    # Initalize all accounts
    for account in accounts:
        place_order.init_account(account)

    auto_straddle_strategy = cash_stratergy()
    auto_straddle_strategy.sync_cash_strategy()
    auto_straddle_strategy.execute_strategy(place_order)  # Pass the place_order argument
"""
