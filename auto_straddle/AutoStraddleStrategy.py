import traceback
from datetime import datetime, time, timedelta
import os
from PlaceOrder import PlaceOrder
import pandas as pd
import TelegramSend

# basic logging configuration
import logging
from OptionChainData import OptionChainData, UnderlyingSymbol

logging.basicConfig(filename='/tmp/auostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)


# For nifty return 50, for bank nifty return 100, for finnifty return 50
def get_strike_interval(symbol):
    if symbol == UnderlyingSymbol.NIFTY:
        return 50
    elif symbol == UnderlyingSymbol.BANKNIFTY:
        return 100
    elif symbol == UnderlyingSymbol.FINNIFTY:
        return 50
    else:
        return 0


class AutoStraddleStrategy:
    def __init__(self, accounts, symbols):
        self.accounts = accounts
        self.symbols = symbols

    def execute_strategy(self, option_chain_analyzer, symbol, account):
        try:
            if account not in self.accounts:
                raise ValueError(f"Error: Account '{account}' not valid. Choose from {self.accounts}")

            # Extract relevant information from option_chain_analyzer
            spot_price = option_chain_analyzer['spot_price']
            atm_ce_strike = option_chain_analyzer['atm_strike']
            atm_pe_strike = option_chain_analyzer['atm_strike']

            # Implement your auto straddle strategy logic here
            # Example: Sell ATM call and put options after 9:30 AM
            current_time = datetime.now().time()

            if current_time > time(15, 20):
                sold_options_file_path = self.get_sold_options_file_path(account, symbol)
                if os.path.exists(sold_options_file_path):
                    # If the file exists, read its contents and populate sold_options_info
                    existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

                    # Close all trade as market is about to close
                    if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':

                        # Close the trade
                        self.close_trade(account, existing_sold_options_info.iloc[-1]['atm_pe_strike'], \
                                         existing_sold_options_info.iloc[-1]['atm_ce_strike'],\
                                              existing_sold_options_info.iloc[-1]['atm_pe_price'],
                                         existing_sold_options_info.iloc[-1]['atm_ce_price'], symbol)

                        if existing_sold_options_info.iloc[-1]['atm_ce_price'] == \
                                -1 or existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_next_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_next_pe_price']
                        else:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_pe_price']

                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = \
                            'closed'
                        existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = \
                            datetime.now()
                        print(f"Auto Straddle trade closed for account {account}")
                        logging.info(
                            f"Auto Straddle trade closed for account {account} {symbol} {option_chain_analyzer['spot_price']}\
                                {option_chain_analyzer['pe_to_ce_ratio']}")

                        compute_profit_loss = self.compute_profit_loss(existing_sold_options_info, symbol)

                        x = TelegramSend.telegram_send_api()

                        # Send profit loss over telegramsend send_message
                        x.send_message("-4008545231", f"Profit or loss for {account} {symbol} is {compute_profit_loss}")

                        # Store the information in a file with account and symbol in the name
                        self.store_sold_options_info(existing_sold_options_info, account, symbol)
                        x.send_file("-4008545231", sold_options_file_path)

                        # Since trade is closed rename the file to sold_options_info_closed
                        os.rename(sold_options_file_path,
                                  sold_options_file_path.replace("sold_options_info", "sold_options_info_closed"))

                    else:
                        compute_profit_loss = self.compute_profit_loss(existing_sold_options_info, symbol)

                        x = TelegramSend.telegram_send_api()

                        # Send profit loss over telegramsend send_message
                        x.send_message("-4008545231", f"Profit or loss for {account} {symbol} is {compute_profit_loss}")

                        # Store the information in a file with account and symbol in the name
                        self.store_sold_options_info(existing_sold_options_info, account, symbol)
                        x.send_file("-4008545231", sold_options_file_path)

                        # Since trade is closed rename the file to sold_options_info_closed
                        os.rename(sold_options_file_path,
                                  sold_options_file_path.replace("sold_options_info", "sold_options_info_closed"))

                return
            elif current_time > time(9, 30):
                # Execute strategy only after 9:30 AM

                # Example: Print a message for demonstration purposes
                print(f"Selling ATM call and put options for account {account} and symbol {symbol}")
                # logging.info(f"Selling ATM call and put options for account {account} and symbol {symbol}")

                # Check if the file exists for the given account, symbol, and date
                sold_options_file_path = self.get_sold_options_file_path(account, symbol)
                if os.path.exists(sold_options_file_path):
                    # If the file exists, read its contents and populate sold_options_info
                    existing_sold_options_info = self.read_existing_sold_options_info(sold_options_file_path)

                    # If the trade is closed, check if the conditions to re-enter the trade are met
                    if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                        print(
                            f"Auto Straddle trade is still open for account {option_chain_analyzer['prev_atm_strike']}")
                        print(
                            f"Auto Straddle trade is still open for account {option_chain_analyzer['prev_atm_strike']}")
                        # logging.info(f"Auto Straddle CE trade is still open for account {option_chain_analyzer['given_ce_strike']}")
                        # logging.info(f"Auto Straddle PE trade is still open for account {option_chain_analyzer['given_pe_strike']}")
                        if existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1 or \
                                existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                            print(option_chain_analyzer['prev_atm_next_pe_price'] , option_chain_analyzer['prev_atm_next_ce_price'])
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_next_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_next_pe_price']
                            
                        else:
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_ce_close_price'] = \
                                option_chain_analyzer['prev_atm_ce_price']
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'atm_pe_close_price'] = \
                                option_chain_analyzer['prev_atm_pe_price']

                        profit_or_loss = self.compute_profit_loss(existing_sold_options_info, symbol)

                        # Check if the conditions to close the trade are met
                        if self.should_close_trade(option_chain_analyzer, existing_sold_options_info.iloc[-1], symbol) \
                                or profit_or_loss < -3000:
                            print(option_chain_analyzer['prev_atm_pe_price'], option_chain_analyzer['prev_atm_ce_price'])
                            # Close the trade
                            self.close_trade(account, existing_sold_options_info.iloc[-1]['atm_pe_strike'], \
                                             existing_sold_options_info.iloc[-1]['atm_ce_strike'], \
                                                existing_sold_options_info.iloc[-1]['atm_pe_price'],
                                             existing_sold_options_info.iloc[-1]['atm_ce_price'], symbol)
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'trade_state'] = \
                                'closed'
                            existing_sold_options_info.loc[existing_sold_options_info.index[-1], 'close_time'] = \
                                datetime.now()
                            print(f"Auto Straddle trade closed for account {account}")
                            logging.info(f"Auto Straddle trade closed for account {account} {symbol} \
                                {option_chain_analyzer['spot_price']} {option_chain_analyzer['pe_to_ce_ratio']}")
                        else:
                            # Conditions to close the trade are not met
                            print(f"Auto Straddle trade is still open for account {account}")
                            # logging.info(f"Auto Straddle trade is still open for account {account}")
                    elif existing_sold_options_info.iloc[-1]['trade_state'] == 'closed':
                        # Check if the conditions to re-enter the trade are met
                        if self.should_reenter_trade(existing_sold_options_info):
                            # Re-enter the trade
                            sold_options_info = {
                                'account': account,
                                'symbol': symbol,
                                'atm_strike': spot_price,
                                'atm_ce_price': get_option_price(option_chain_analyzer, 'CE'),
                                'atm_pe_price': get_option_price(option_chain_analyzer, 'PE'),
                                'trade_state': 'open',
                                'open_time': datetime.now(),
                                'close_time': None,
                                'atm_ce_strike': get_option_strike(option_chain_analyzer, 'CE', symbol),
                                'atm_pe_strike': get_option_strike(option_chain_analyzer, 'PE', symbol),
                                'atm_ce_close_price': get_option_price(option_chain_analyzer, 'CE'),
                                'atm_pe_close_price': get_option_price(option_chain_analyzer, 'PE'),
                            }

                            # Place orders for ATM CE and ATM PE, if pe is less than 0.7 place only CE order and\
                            # if pe greater than 1.4 place only PE order else place both orders
                            if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
                                # Place only CE order
                                sold_options_info['atm_pe_price'] = -1
                                PlaceOrder.place_orders(account, atm_ce_strike + get_strike_interval(symbol), 'CE')
                            elif option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
                                # Place only PE order
                                sold_options_info['atm_ce_price'] = -1
                                PlaceOrder.place_orders(account, atm_pe_strike - get_strike_interval(symbol), 'PE')
                            else:
                                # Place both CE and PE orders
                                PlaceOrder.place_orders(account, atm_ce_strike, 'CE')
                                PlaceOrder.place_orders(account, atm_pe_strike, 'PE')

                            print(f"Auto Straddle trade re-entered for account {account}")
                            logging.info(f"Auto Straddle trade re-entered for account {account} \
                                         {option_chain_analyzer['pe_to_ce_ratio']} {symbol} {option_chain_analyzer['spot_price']}")
                            logging.info(f"Auto Straddle {get_option_price(option_chain_analyzer, 'CE')} \
                                    {get_option_price(option_chain_analyzer, 'PE')}")
                            existing_sold_options_info = pd.concat(
                                [existing_sold_options_info, pd.DataFrame([sold_options_info])], ignore_index=True)
                        else:
                            # Conditions to re-enter the trade are not met
                            print(f"Auto Straddle trade is still closed for account {account}")
                            logging.info(f"Auto Straddle trade is still closed for account {account}")

                else:
                    # If the file doesn't exist, create a new sold_options_info
                    existing_sold_options_info = pd.DataFrame()
                    sold_options_info = {
                        'account': account,
                        'symbol': symbol,
                        'atm_strike': spot_price,
                        'atm_ce_price': get_option_price(option_chain_analyzer, 'CE'),
                        'atm_pe_price': get_option_price(option_chain_analyzer, 'PE'),
                        'trade_state': 'open',
                        'open_time': datetime.now(),
                        'close_time': None,
                        'atm_ce_strike': get_option_strike(option_chain_analyzer, 'CE', symbol),
                        'atm_pe_strike': get_option_strike(option_chain_analyzer, 'PE', symbol),
                        'atm_ce_close_price': get_option_price(option_chain_analyzer, 'CE'),
                        'atm_pe_close_price': get_option_price(option_chain_analyzer, 'PE'),
                    }

                    # Place orders for ATM CE and ATM PE, if pe is less than 0.7 place only CE order
                    # and if pe greater than 1.4 place only PE order else place both orders
                    logging.info(f"Auto Straddle {symbol} {get_option_price(option_chain_analyzer, 'CE')} {get_option_price(option_chain_analyzer, 'PE')}")
                    logging.info(f"Auto Straddle {option_chain_analyzer['pe_to_ce_ratio']} {symbol} {option_chain_analyzer['spot_price']}")
                    if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
                        # Place only CE order
                        sold_options_info['atm_pe_price'] = -1
                        PlaceOrder.place_orders(account, atm_ce_strike + get_strike_interval(symbol), 'CE')
                    elif option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
                        # Place only PE order
                        sold_options_info['atm_ce_price'] = -1
                        PlaceOrder.place_orders(account, atm_pe_strike - get_strike_interval(symbol), 'PE')
                    else:
                        # Place both CE and PE orders
                        PlaceOrder.place_orders(account, atm_ce_strike, 'CE')
                        PlaceOrder.place_orders(account, atm_pe_strike, 'PE')

                    # convert store_sold_options_info to dataframe
                    existing_sold_options_info = pd.concat([existing_sold_options_info,
                                                            pd.DataFrame([sold_options_info])], ignore_index=True)

                # Store the information in a file with account and symbol in the name
                self.store_sold_options_info(existing_sold_options_info, account, symbol)

        except Exception as e:
            print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            logging.error(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
            print(f"Error executing Auto Straddle Strategy: {e}")
            logging.error(f"Error executing Auto Straddle Strategy: {e}")

    def get_sold_options_file_path(self, account, symbol):
        current_date = datetime.now().strftime("%Y-%m-%d")
        file_name = f"as_sold_options_info_{current_date}_{account}_{symbol}.csv"
        return file_name

    # Function computes profit or loss of existing_sold_options_info by subtracting each row of 
    # atm_ce_price and atm_pe_price from atm_ce_close_price and atm_pe_close_price respectively
    def compute_profit_loss(self, existing_sold_options_info, symbol):
        try:
            # Define multiplication factors based on the symbol
            multiplication_factor = {
                'UnderlyingSymbol.NIFTY': 50,
                'UnderlyingSymbol.BANKNIFTY': 15,
                'UnderlyingSymbol.FINNIFTY': 40
            }
            total_profit_loss = 0

            # Iterate over all rows and compute profit/loss for each row
            for index, row in existing_sold_options_info.iterrows():
                # Extract relevant columns from the current row
                atm_ce_price = row['atm_ce_price']
                atm_pe_price = row['atm_pe_price']
                atm_ce_close_price = row['atm_ce_close_price']
                atm_pe_close_price = row['atm_pe_close_price']
                profit_loss_ce = 0
                profit_loss_pe = 0

                # Compute profit or loss for the current row
                if atm_ce_price != -1:
                    # CE order was not placed
                    profit_loss_ce = atm_ce_price - atm_ce_close_price

                if atm_pe_price != -1:
                    # PE order was not placed
                    profit_loss_pe = atm_pe_price - atm_pe_close_price

                # Sum up the profit or loss for the current row
                total_profit_loss = total_profit_loss + profit_loss_ce + profit_loss_pe

            # Multiply the total profit or loss by the factor based on the symbol
            total_profit_loss *= multiplication_factor.get(str(symbol), 1)

            print(f"Total profit or loss: {total_profit_loss}")
            logging.info(f"{symbol} Current total profit or loss: {total_profit_loss}")

            # Check if the total loss is more than 3000
            return total_profit_loss

        except Exception as e:
            print(f"Error computing profit or loss: {e}")
            logging.error(f"Error computing profit or loss: {e}")
            return None, None

    def read_existing_sold_options_info(self, file_path):
        try:
            # Read existing content from the CSV file into a DataFrame
            if os.path.exists(file_path):
                df = pd.read_csv(file_path)
                return df
            else:
                print(f"Warning: File '{file_path}' does not exist.")
                logging.warning(f"Warning: File '{file_path}' does not exist.")
                return None

        except Exception as e:
            print(f"Error reading sold options information: {e}")
            logging.error(f"Error reading sold options information: {e}")
            return None

    def should_close_trade(self, option_chain_analyzer, sold_options_info, symbol):
        # Implement conditions to close the trade based on spot price movement
        # Example: Close the trade if NIFTY spot_price has moved by 60, FINNIFTY by 60, and BANKNIFTY by 120
        nifty_movement = 60
        finnifty_movement = 60
        banknifty_movement = 120

        if (
                symbol == UnderlyingSymbol.NIFTY
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= nifty_movement
        ) or (
                symbol == UnderlyingSymbol.FINNIFTY
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= finnifty_movement
        ) or (
                symbol == UnderlyingSymbol.BANKNIFTY
                and abs(option_chain_analyzer['spot_price'] - sold_options_info['atm_strike']) >= banknifty_movement
        ):
            logging.info(
                f"Closing the trade for account {symbol} {option_chain_analyzer['spot_price']} from {sold_options_info['atm_strike']}")
            return True
        return False

    def close_trade(self, account, pe_strike, ce_strike, atm_pe_price, atm_ce_price, symbol):
        # Close the trade logic goes here
        print(f"Closing the trade for account {account}" , str(symbol) , str(atm_pe_price) , str(atm_ce_price) , str(ce_strike) , str(pe_strike))
        logging.info(f"Closing the trade for account {account}")
        if (atm_pe_price != -1):
            PlaceOrder.close_orders(account, pe_strike, 'PE', symbol)
        if (atm_ce_price != -1):
            PlaceOrder.close_orders(account, ce_strike, 'CE', symbol)
        # Update the trade state

    def store_sold_options_info(self, info, account, symbol):
        try:
            file_name = self.get_sold_options_file_path(account, symbol)

            # Save data_frame to CSV with the current date appended to the symbol
            info.to_csv(file_name, index=False)

            print(f"Sold options information stored in {file_name}")
            # logging.info(f"Sold options information stored in {file_name}")

        except Exception as e:
            print(f"Error storing sold options information: {e}")
            logging.error(f"Error storing sold options information: {e}")

    def get_strike_price(self, account, symbol):
        print(f"Getting strike price for account {account} and symbol {symbol}")
        # logging.info(f"Getting strike price for account {account} and symbol {symbol}")
        current_date = datetime.now().strftime("%Y-%m-%d")
        file_name = self.get_sold_options_file_path(account, symbol)
        if os.path.exists(file_name):
            existing_sold_options_info = self.read_existing_sold_options_info(file_name)
            if existing_sold_options_info.iloc[-1]['trade_state'] == 'open':
                if existing_sold_options_info.iloc[-1]['atm_pe_price'] == -1:
                    return existing_sold_options_info.iloc[-1]['atm_pe_strike']
                elif existing_sold_options_info.iloc[-1]['atm_ce_price'] == -1:
                    return existing_sold_options_info.iloc[-1]['atm_ce_strike']
                else:
                    return existing_sold_options_info.iloc[-1]['atm_ce_strike']
            else:
                return 0
        else:
            print("File does not exists file_name = ", file_name)
            #logging.error("File does not exists file_name = %s", file_name)
            return 0

    def should_reenter_trade(self, sold_options_info):

        profit_amount = self.compute_profit_loss(sold_options_info, sold_options_info.iloc[-1]['symbol'])
        if profit_amount < -3000:
            print(f"Profit amount: {profit_amount} is greater than 3000")
            logging.info(f"Profit amount: {profit_amount} is greater than 3000")
            return False

        # sold_options_info has more than 1 row
        if sold_options_info.shape[0] >= 5:
            return False

        # Check if the required 5-minute interval has passed since the trade close time
        if (
                sold_options_info.iloc[-1]['close_time'] and sold_options_info.iloc[-1]['trade_state'] == 'closed'
                and (
                datetime.now() - datetime.strptime(sold_options_info.iloc[-1]['close_time'], '%Y-%m-%d %H:%M:%S.%f'))
                >= timedelta(minutes=5)
        ):
            return True
        return False


def get_option_price(option_chain_analyzer, option_type):
    # Implement your logic to get the option price based on strike price and type (CE/PE)
    # You can extract this information from option_chain_analyzer
    # For example, option_chain_analyzer['CE'] and option_chain_analyzer['PE']
    # return option_price from option_chain_analyzer
    if option_type == 'CE':
        if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
            return option_chain_analyzer['atm_next_ce_price']
        else:
            return option_chain_analyzer['atm_current_ce_price']
    elif option_type == 'PE':
        if option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
            return option_chain_analyzer['atm_next_pe_price']
        else:
            return option_chain_analyzer['atm_current_pe_price']
        

def get_option_strike(option_chain_analyzer, option_type, symbol):
    # Implement your logic to get the option price based on strike price and type (CE/PE)
    # You can extract this information from option_chain_analyzer
    # For example, option_chain_analyzer['CE'] and option_chain_analyzer['PE']
    # return option_price from option_chain_analyzer
    if option_type == 'CE':
        if option_chain_analyzer['pe_to_ce_ratio'] < 0.7:
            return option_chain_analyzer['atm_strike'] + get_strike_interval(symbol)
        else:
            return option_chain_analyzer['atm_strike']
    elif option_type == 'PE':
        if option_chain_analyzer['pe_to_ce_ratio'] > 1.4:
            return option_chain_analyzer['atm_strike'] - get_strike_interval(symbol)
        else:
            return option_chain_analyzer['atm_strike']


"""
accounts = ["Account1", "Account2"]
symbols = [UnderlyingSymbol.NIFTY]

for symbol in symbols:
    option_chain_analyzer = OptionChainData(symbol)

    auto_straddle_strategy = AutoStraddleStrategy(accounts, symbol)
    strike_data = auto_straddle_strategy.get_strike_price(accounts[0], symbol)
    print(f"Strike data: {strike_data}")
    # If symbol is nifty, use the following line to get the option chain data
    option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data, 0, 0)
    #print(f"Option chain info: {option_chain_info}")

    if option_chain_info is not None:
        print(f"pe_to_ce_ratio: {option_chain_info['pe_to_ce_ratio']}")
        #option_chain_info['pe_to_ce_ratio'] = 0.6
        auto_straddle_strategy.execute_strategy(option_chain_info, symbol, "Account1")
    else:
        print(f"Option chain information not available for symbol {symbol}")
"""
