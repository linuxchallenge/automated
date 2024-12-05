"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105



from datetime import datetime
import logging
#from PlaceOrder import PlaceOrder
import pandas as pd
from yahoo_fin import stock_info  # Install via `pip install yahoo_fin`

#from OptionChainData import OptionChainData
#from pathlib import Path
#from PlaceOrder import PlaceOrder

# Set up logging
logger = logging.getLogger(__name__)

class cash_stratergy:
    def __init__(self):
        self.csv_path = "cash_stratergy.csv"
        self.remote_csv_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSmdwtCAt2oAYnuJGBb3zp7L0Q-iYSZoCMLvy3cfLrz48kp9cHvBqPjRp_p7uRc0Muw_lE7kl0wOnNP/pub?output=csv"
        self.execution_tracker = {"morning": 0, "afternoon": 0}

    def sync_cash_strategy(self):
        """
        Syncs the remote CSV with the local CSV based on today's date and updates
        or inserts rows accordingly.
        """
        # Step 1: Download the CSV from the remote URL
        remote_data = pd.read_csv(self.remote_csv_url)
        remote_data['open_date'] = pd.to_datetime(remote_data['open_date'], errors='coerce')

        # Step 2: Load the local CSV
        try:
            local_data = pd.read_csv(self.csv_path)
        except FileNotFoundError:
            print("Local CSV not found. Creating a new one.")
            local_data = pd.DataFrame(columns=remote_data.columns)

        # Step 3: Filter rows for today's date
        today_date = datetime.now().date()
        today_rows = remote_data[remote_data['open_date'].dt.date == today_date]

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
        # Determine if the function can execute based on the time of day
        now = datetime.now()
        if now.time() < datetime.strptime("09:45:00", "%H:%M:%S").time():
            if self.execution_tracker["morning"] >= max_executions:
                print("Morning execution limit reached.")
                return
            self.execution_tracker["morning"] += 1
        elif now.time() > datetime.strptime("13:00:00", "%H:%M:%S").time():
            if self.execution_tracker["afternoon"] >= max_executions:
                print("Afternoon execution limit reached.")
                return
            self.execution_tracker["afternoon"] += 1
        else:
            print("Not within execution time windows.")
            return

        # Load the CSV
        data = pd.read_csv(self.csv_path)

        # Step 2.4: Process rows with status 'new'
        for idx, row in data[data['status'] == 'new'].iterrows():
            try:
                last_price = stock_info.get_live_price(row['symbol'])
                quantity = int(row['amount'] / last_price)
                order_id = place_order.place_cash_order(row['account'], row['symbol'], quantity, "buy")

                # Update the row in the DataFrame
                data.loc[idx, 'buy_order_id'] = order_id
                data.loc[idx, 'buy_price'] = last_price
                data.loc[idx, 'open_order_status'] = 'open_pending'
                data.loc[idx, 'status'] = 'open_pending'
            except Exception as e:
                print(f"Error processing 'new' row {row['sl_no']}: {e}")

        # Step 2.5: Process rows with status 'open'
        for idx, row in data[data['status'] == 'open'].iterrows():
            try:
                last_price = stock_info.get_live_price(row['symbol'])
                if last_price <= row['sl'] or last_price >= row['profit_target']:
                    order_id = place_order.place_cash_order(row['account'], row['symbol'], row['quantity'], "sell")

                    # Update the row in the DataFrame
                    data.loc[idx, 'close_order_id'] = order_id
                    data.loc[idx, 'close_order_status'] = 'close_pending'
                    data.loc[idx, 'status'] = 'close_pending'
            except Exception as e:
                print(f"Error processing 'open' row {row['sl_no']}: {e}")

        # Step 2.6: Process rows with status 'open_pending' or 'close_pending'
        for idx, row in data[data['status'].isin(['open_pending', 'close_pending'])].iterrows():
            try:
                order_id = row['buy_order_id'] if row['status'] == 'open_pending' else row['close_order_id']
                status = place_order.order_status(row['account'], order_id, row['buy_price'])

                if status == "Complete":
                    data.loc[idx, 'status'] = 'open' if row['status'] == 'open_pending' else 'close'
                    data.loc[idx, 'open_order_status' if row['status'] == 'open_pending' else 'close_order_status'] = 'Complete'
            except Exception as e:
                print(f"Error processing 'pending' row {row['sl_no']}: {e}")

        # Save the updated CSV
        data.to_csv(self.csv_path, index=False)
        print("Strategy executed and CSV updated.")


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
