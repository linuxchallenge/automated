"""Module providing a function for main function """

# pylint: disable=W1203
# pylint: disable=W1201
# pylint: disable=W1202
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103


import time
from datetime import datetime
import logging
import os
from pathlib import Path
import traceback
import pandas as pd
from PlaceOrder import PlaceOrder
from OptionChainData import OptionChainData
from AutoStraddleStrategy import AutoStraddleStrategy
from FarSellStratergy import FarSellStratergy


logging.basicConfig(filename='/tmp/autostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)


def main():
    # Replace these lists with your desired accounts and symbols
    accounts = []
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY"]

    logging.info("Starting the program, welcome to AutoStraddle")

    # Get home directory
    cur_dir = Path.home()
    # Add /temp/data_collection to the home directory
    cur_dir = cur_dir / 'temp' / 'data_collection'
    # Create the directory if it does not exist
    cur_dir.mkdir(parents=True, exist_ok=True)

    #Change the current working directory to the directory
    os.chdir(cur_dir)

    # Create instances of OptionChainData and AutoStraddleStrategy
    auto_straddle_strategy = AutoStraddleStrategy(accounts, symbols)
    farsell_straddle_strategy = FarSellStratergy(accounts, symbols)

    path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQt7b9qZSCk8Un-5nTeOKyiaCNZPjeRLQHv41f8J2JVrXCvNPhaXtuoZEXEz7o3O4NG_ltFCjimld8Y/pub?output=csv'
    #path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTbpF19Et4qAM5OECrRCEMyb2s5x6R6Im9XXwxrTbLi097-QpLMc3aPcpWO7OF6QTOwUHce91zQPkU8/pub?output=csv'
    account_details = pd.read_csv(path)

    logging.info("Account details from google sheet")
    logging.info(account_details)
    print(account_details)

    # Append accounts with data from google sheet
    for _, row in account_details.iterrows():
        accounts.append(row['Account'])

    # Remove duplicates
    accounts = list(dict.fromkeys(accounts))
    print(accounts)
    logging.info("Accounts: %s", str(accounts))

    # Create an instance of PlaceOrder
    place_order = PlaceOrder()

    logging.info("After creating instance of PlaceOrder")

    # Initalize all accounts
    for account in accounts:
        logging.info("Initializing account: %s", account)
        print("Initializing account: ", account)
        place_order.init_account(account)

    logging.info("After initializing all accounts")

    try:
        while True:
            try:
                # Get current time
                current_time = datetime.now().second
                for symbol in symbols:
                    option_chain_analyzer = OptionChainData(symbol)

                    strike_data = auto_straddle_strategy.get_strike_price(accounts[0], symbol)
                    pe_strike, ce_strike = farsell_straddle_strategy.get_strangle_strike_price(accounts[0], symbol)

                    # Get option chain data for the specified symbol
                    option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data, ce_strike, pe_strike)

                    # Dump option_chain_analyzer data to a CSV file with file name of symbol and date.
                    # dump_option_chain_data_to_csv(option_chain_info, symbol)

                    if option_chain_info is not None:
                        for account in accounts:
                            # Execute the strategy for the current account and symbol
                            # In account_details if for symbol and account, stratergy is 'as' then execute auto straddle strategy
                            # if stratergy is 'fr' then execute far sell strategy
                            if account_details.loc[
                                (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                                & (account_details['Stratergy'] == 'as')].shape[0] > 0:
                                quantity = account_details.loc[
                                    (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                                    & (account_details['Stratergy'] == 'as')]['quantity'].values[0]
                                if quantity > 0:
                                    auto_straddle_strategy.execute_strategy(option_chain_info, symbol, account,
                                                                            quantity, place_order)

                            if account_details.loc[
                                (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                                & (account_details['Stratergy'] == 'fr')].shape[0] > 0:
                                quantity = account_details.loc[
                                    (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                                    & (account_details['Stratergy'] == 'fr')]['quantity'].values[0]
                                if quantity > 0:
                                    farsell_straddle_strategy.execute_strategy(option_chain_info, symbol, account,
                                                                               quantity, place_order)

                    else:
                        print("Option chain data is not available for the symbol: " + symbol)
                        logging.error("Option chain data is not available for the symbol: " + symbol)

                # Sleep for a specified interval (e.g., 1 minutes)
                after_loop_time = datetime.now().second
                time.sleep(60 - (after_loop_time - current_time))
            except Exception as e:
                logging.error(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
                print(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
                time.sleep(55)
                continue

    except KeyboardInterrupt:
        print("Exiting the program.")


# Funcion to dump option chain data to a CSV file with file name of symbol and date.
def dump_option_chain_data_to_csv(option_chain_info, symbol):
    # Get current date and time

    # Create a file name with symbol and date
    current_date = datetime.now().strftime("%Y-%m-%d")
    file_name = f"options_chain_{symbol}_{current_date}.csv"

    # Check file exists, if read from file and append new data
    # if not create a new file and write data

    if os.path.exists(file_name):
        data_frame = pd.read_csv(file_name)
    else:
        data_frame = pd.DataFrame()

    # convert option_chain_info to dataframe
    # option_chain_info = option_chain_info_from_file.append(pd.DataFrame([option_chain_info]))
    data_frame = pd.concat([data_frame, pd.DataFrame([option_chain_info])], ignore_index=True)

    # Dump option chain data to a CSV file
    data_frame.to_csv(file_name, index=False)


if __name__ == "__main__":
    main()
