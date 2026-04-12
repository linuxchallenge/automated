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
import signal
from datetime import datetime, timedelta
from datetime import time as time_dt
import logging
import os
from pathlib import Path
import traceback
from io import StringIO
import pandas as pd
import requests
from PlaceOrder import PlaceOrder
from OptionChainData import OptionChainData
from AutoStraddleStrategy import AutoStraddleStrategy
from FarSellStratergy import FarSellStratergy
import configuration
from CommodityStratergy import CommodityStratergy
from cash_stratergy import cash_stratergy
from IndexFutureStratergy import IndexFutureStratergy
from NiftyPositionalStrategy import NiftyPositionalStrategy
#from optionbuy_stratergy import OptionBuyStrategy
import logging_config  # pylint: disable=unused-import  # side-effect: configures logging
from TelegramSend import telegram_send_api
from ledger_calculation import LedgerCalculator
from update_cash_sl import run_cash_sl_update

# Set up logging
logger = logging.getLogger(__name__)
strike = {'NIFTY': 23000, 'BANKNIFTY': 49000, 'FINNIFTY': 15000}

# Define the timeout handler
def timeout_handler(_signum, _frame):
    print("Timeout! The operation took too long.")
    logger.error("Timeout! The operation took too long.")
    # Throw exception to exit the program

    x = telegram_send_api()

    telegram_group = "deepti" + "_telegram"

    id3 = configuration.ConfigurationLoader.get_configuration().get(telegram_group)

    x.send_message(id3, "Timeout! The operation took too long restart the program.")

    raise TimeoutError("Operation took too long to complete")


def read_csv_from_google_sheet(url, max_retries=3):
    """Read CSV from Google Sheets with proper headers"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            # Parse CSV content
            csv_content = StringIO(response.text)
            df = pd.read_csv(csv_content)
            return df

        except Exception as e:
            logging.error("Attempt %d failed to read Google Sheet: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2)  # Wait before retry
            else:
                raise e

def main():
    # Replace these lists with your desired accounts and symbols
    accounts = []
    accounts_commodity = []
    accounts_index = []
    accounts_optionbuy = []
    accounts_niftyposition = []
    symbols = ["NIFTY", "BANKNIFTY"]

    current_time_dt = datetime.now().time()
    if current_time_dt > time_dt(23, 45):
        time.sleep(60)
        print("Exiting the program.")
        logging.info("Exiting the program.")
        exit(1)

    logging.info("Starting the program, welcome to AutoStraddle")

    configuration.ConfigurationLoader.load_configuration()

    logging.info("After loading configuration")
    while True:
        current_time_dt = datetime.now().time()

        if current_time_dt < time_dt(8, 55):
            time.sleep(60)
            continue

        current_time_week = datetime.now()

        # If sat or sun, then break the loop
        if current_time_week.weekday() == 5 or current_time_week.weekday() == 6:
            time.sleep(60)
            if current_time_dt > time_dt(23, 50):
                print("Exiting the program.")
                logging.info("Exiting the program.")
                exit(1)
            continue

        break

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

    commodity_stratergy = CommodityStratergy(accounts_commodity)

    try:
        path = 'https://docs.google.com/spreadsheets/d/1Kndwbk4S9iSz9uZ4ZaMkPG2bHehjqRWU7RdJ595jwQg/export?format=csv'
        account_details = read_csv_from_google_sheet(path)

        logging.info("Account details from google sheet")
        logging.info(account_details)
        print(account_details)

    except Exception as e:
        logging.error("Failed to read account details from Google Sheet: %s", e)
        # Fallback to local file or exit
        print("Failed to read Google Sheet, using fallback data or exiting...")
        return

    # Continue with other sheets...
    try:
        coomodity_path = 'https://docs.google.com/spreadsheets/d/12hH-wMr36t7VGiyO08oAbaihyOCt6ZPLKj7FO9wNH6o/export?format=csv'
        commodity_account_details = read_csv_from_google_sheet(coomodity_path)
        print(commodity_account_details)

        index_path = 'https://docs.google.com/spreadsheets/d/1S2PO_tPjnCpq3LGWRUXJenWouSAJ8dxCuC_jTLSC07E/export?format=csv'
        index_account_details = read_csv_from_google_sheet(index_path)
        print(index_account_details)

        optionbuy_path = 'https://docs.google.com/spreadsheets/d/1IdB6YTBDLbyMTzwJBW0gWnvFuHc3Q-r_nem0RcSU_ao/export?format=csv'
        optionbuy_account_details = read_csv_from_google_sheet(optionbuy_path)
        print(optionbuy_account_details)

        niftyposition_path = 'https://docs.google.com/spreadsheets/d/1Ncv-9eA52t6bMNIcI3kzAxTQlvjQ9dOvqZdFX0-JYCM/export?format=csv'
        nifty_position_account_details = read_csv_from_google_sheet(niftyposition_path)
        print(nifty_position_account_details)

    except Exception as e:
        logging.error("Failed to read one or more Google Sheets: %s", e)
        # Handle fallback logic here

    # Append accounts with data from google sheet
    for _, row in account_details.iterrows():
        accounts.append(row['Account'])

    # Append accounts with data from google sheet
    for _, row in commodity_account_details.iterrows():
        accounts_commodity.append(row['Account'])

    for _, row in index_account_details.iterrows():
        accounts_index.append(row['Account'])

    for _, row in optionbuy_account_details.iterrows():
        accounts_optionbuy.append(row['Account'])

    # Append accounts with data from google sheet
    for _, row in nifty_position_account_details.iterrows():
        accounts_niftyposition.append(row['Account'])

    # Remove duplicates
    accounts = list(dict.fromkeys(accounts))
    print(accounts)
    logging.info("Accounts: %s", str(accounts))

    accounts_commodity = list(dict.fromkeys(accounts_commodity))

    accounts_index = list(dict.fromkeys(accounts_index))

    accounts_optionbuy = list(dict.fromkeys(accounts_optionbuy))

    accounts_niftyposition = list(dict.fromkeys(accounts_niftyposition))

    #merge accounts and accounts_commodity
    accounts_merged = accounts + accounts_commodity + accounts_index + \
        accounts_optionbuy + accounts_niftyposition

    # remove duplicates of accounts_merged
    accounts_merged = list(dict.fromkeys(accounts_merged))

    # Create an instance of PlaceOrder
    place_order = PlaceOrder()

    cash_stratergy_obj = cash_stratergy()
    cash_stratergy_obj.sync_cash_strategy()

    logging.info("After creating instance of PlaceOrder")

    # Initalize all accounts
    for account in accounts_merged:
        logging.info("Initializing account: %s", account)
        print("Initializing account: ", account)
        place_order.init_account(account)

    logging.info("After initializing all accounts")

    # Import and initialize ledger calculator
    ledger_calculator = LedgerCalculator()

    # Calculate and track ledger for current day
    current_day = datetime.now().strftime("%Y-%m-%d")

    try:
        logger.info("Starting ledger balance check for %s", current_day)
        # This now handles both CSV update and Telegram sending
        ledger_calculator.generate_ledger_with_balance_check(current_day, place_order)
        logger.info("Ledger balance check completed successfully")

    except Exception as e:
        logger.error("Error in ledger calculation: %s", e)
        logger.error(traceback.format_exc())

    index_future_stratergy = IndexFutureStratergy(accounts_index)

    nifty_position_stratergy = NiftyPositionalStrategy(accounts_niftyposition)

    #optionbuy_stratergy = OptionBuyStrategy()

    # Set the signal handler (Linux-only — pylint suppressed as this runs on Raspberry Pi)
    signal.signal(signal.SIGALRM, timeout_handler)  # pylint: disable=no-member

    # Set an alarm to trigger SIGALRM after 300 seconds
    signal.alarm(300)  # pylint: disable=no-member

    # Flag to track if Cash SL update has run today (runs at 11:10 PM)
    cash_sl_updated_today = False

    try:
        while True:
            try:

                current_time_dt = datetime.now().time()

                if current_time_dt < time_dt(9, 00):
                    time.sleep(60)
                    continue

                # Get current time
                current_time = datetime.now().second

                # Reset alarm for index future strategy (runs at 9:15+)
                signal.alarm(300)  # pylint: disable=no-member
                try:
                    index_future_stratergy.execute_strategy(accounts_index, place_order, index_account_details)
                except Exception as e:
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Reset alarm for nifty position strategy (runs at 9:15+, must be before option strategy)
                signal.alarm(300)  # pylint: disable=no-member
                try:
                    nifty_position_stratergy.execute_strategy(place_order, nifty_position_account_details)
                except Exception as e:
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Reset alarm for option strategy (has 60s sleep if time < 9:23)
                signal.alarm(300)  # pylint: disable=no-member
                try:
                    execute_option_stratergy(auto_straddle_strategy, farsell_straddle_strategy, \
                                             accounts, symbols, place_order, account_details, \
                                            index_future_stratergy)
                except Exception as e:
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Reset alarm for commodity strategy
                signal.alarm(300)  # pylint: disable=no-member
                try:
                    execute_commity_stratergy(commodity_stratergy, accounts_commodity, place_order, commodity_account_details)
                except Exception as e:
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Commented out option buy strategy execution
                # try:
                #    optionbuy_stratergy.execute_strategy(accounts_optionbuy, place_order, optionbuy_account_details, strike)
                # except Exception as e:
                #    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                #    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Reset alarm before cash strategy (can take long due to rate limiting)
                signal.alarm(300)  # pylint: disable=no-member
                try:
                    cash_stratergy_obj.execute_strategy(place_order)
                except Exception as e:
                    logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Run Cash SL Update at 11:10 PM daily
                if time_dt(23, 10) <= current_time_dt <= time_dt(23, 15):
                    if not cash_sl_updated_today:
                        logging.info("Running Cash SL Update at 11:10 PM")
                        signal.alarm(600)  # pylint: disable=no-member  # 10 minutes for SL update
                        try:
                            run_cash_sl_update(dry_run=False)
                            cash_sl_updated_today = True
                            logging.info("Cash SL Update completed successfully")
                        except Exception as e:
                            logging.error(f"Cash SL Update failed: {e}")
                            logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))

                # Reset the flag at midnight
                if current_time_dt < time_dt(0, 5):
                    cash_sl_updated_today = False

                # Sleep for a specified interval (e.g., 1 minute)
                # Use full timestamp to handle minute boundary correctly
                loop_end_time = datetime.now()
                loop_start_time = loop_end_time.replace(second=current_time, microsecond=0)
                # If we crossed a minute boundary, adjust the start time
                if loop_end_time.second < current_time:
                    loop_start_time = loop_start_time - timedelta(minutes=1)
                elapsed_seconds = (loop_end_time - loop_start_time).total_seconds()
                time_to_sleep = 60 - elapsed_seconds
                if 0 < time_to_sleep <= 60:
                    time.sleep(time_to_sleep)

                if current_time_dt > time_dt(23, 50):
                    print("Exiting the program.")
                    logging.info("Exiting the program.")
                    exit(1)

            except Exception as e:
                logging.error(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                print(''.join(traceback.format_exception(type(e), e, e.__traceback__)))
                time.sleep(55)
                continue

    except KeyboardInterrupt:
        print("Exiting the program.")

def execute_option_stratergy(auto_straddle_strategy, farsell_straddle_strategy, accounts, symbols, place_order, account_details, index_future_stratergy):

    # return if time is outside option strategy window (9:23 AM to 3:29 PM)
    current_time_dt = datetime.now().time()
    if current_time_dt > time_dt(15, 29):
        return

    if current_time_dt < time_dt(9, 23):
        return

    for symbol in symbols:
        option_chain_analyzer = OptionChainData(symbol)

        strike_data = auto_straddle_strategy.get_strike_price(accounts, symbol)
        pe_strike, ce_strike = farsell_straddle_strategy.get_strangle_strike_price(accounts, symbol)

        #print("Before calling get_option_chain_info", strike_data, pe_strike, ce_strike)

        # Get option chain data for the specified symbol
        option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data, ce_strike, pe_strike, symbol)

        strike[symbol] = option_chain_info['atm_strike']

        # Dump option_chain_analyzer data to a CSV file with file name of symbol and date.
        dump_option_chain_data_to_csv(option_chain_info, symbol)

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
                        #print("==== Executing auto straddle strategy for account: " + account + " " + symbol)
                        auto_straddle_strategy.execute_strategy(option_chain_info, symbol, account,
                                                                quantity, place_order, index_future_stratergy)
                        #print("==== Exit auto straddle strategy for account: " + account + " " + symbol)

                if account_details.loc[
                    (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                    & (account_details['Stratergy'] == 'fr')].shape[0] > 0:
                    quantity = account_details.loc[
                        (account_details['Account'] == account) & (account_details['Symbol'] == symbol) \
                        & (account_details['Stratergy'] == 'fr')]['quantity'].values[0]
                    if quantity > 0:
                        #print("==== Executing far sell strategy for account: " + account + " " + symbol)
                        farsell_straddle_strategy.execute_strategy(option_chain_info, symbol, account,
                                                                    quantity, place_order, index_future_stratergy)
                        #print("==== Exit far sell strategy for account: " + account + " " + symbol)

        else:
            print("Option chain data is not available for the symbol: " + symbol)
            logging.error("Option chain data is not available for the symbol: " + symbol)


def execute_commity_stratergy(commodity_stratergy, accounts, place_order, account_details):
    commodity_stratergy.execute_strategy(accounts, place_order, account_details)


# Funcion to dump option chain data to a CSV file with file name of symbol and date.
def dump_option_chain_data_to_csv(option_chain_info, symbol):
    # Get current date and time

    # Create a file name with symbol and date
    current_date = datetime.now().strftime("%Y-%m-%d")
    file_name = f"csv/options_chain_{symbol}_{current_date}.csv"

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
