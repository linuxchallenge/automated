from OptionChainData import OptionChainData
from AutoStraddleStrategy import AutoStraddleStrategy
from FarSellStratergy import FarSellStratergy
from OptionChainData import UnderlyingSymbol
import time
from datetime import datetime
import logging
import pandas as pd
import os

import traceback
logging.basicConfig(filename='/tmp/auostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

logging.getLogger().setLevel(logging.INFO)

def main():
    # Replace these lists with your desired accounts and symbols
    accounts = ['account1']
    symbols = [UnderlyingSymbol.NIFTY, UnderlyingSymbol.BANKNIFTY, UnderlyingSymbol.FINNIFTY]

    # Create instances of OptionChainData and AutoStraddleStrategy
    auto_straddle_strategy = AutoStraddleStrategy(accounts, symbols)
    farsell_straddle_strategy = FarSellStratergy(accounts, symbols)

    try:
        while True:
            try :
                # Get current time
                current_time = datetime.now().second
                for symbol in symbols:
                    option_chain_analyzer = OptionChainData(symbol)

                    strike_data = auto_straddle_strategy.get_strike_price(accounts[0], symbol)
                    pe_strike, ce_strike = farsell_straddle_strategy.get_strangle_strike_price(accounts[0], symbol)

                    # Get option chain data for the specified symbol
                    option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data, ce_strike, pe_strike)

                    # Dump option_chain_analyzer data to a CSV file with file name of symbol and date.
                    dump_option_chain_data_to_csv(option_chain_info, symbol)

                    if option_chain_info is not None:
                        for account in accounts:
                            # Execute the strategy for the current account and symbol
                            auto_straddle_strategy.execute_strategy(option_chain_info, symbol, account)
                            farsell_straddle_strategy.execute_strategy(option_chain_info, symbol, account)
                    else:
                        print("Option chain data is not available for the symbol: " + symbol)
                        logging.error("Option chain data is not available for the symbol: " + symbol)

                # Sleep for a specified interval (e.g., 1 minutes)
                after_loop_time = datetime.now().second
                time.sleep(60 - (after_loop_time - current_time))
            except Exception as e:
                logging.error(''.join(traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)))
                logging.error("Failed: {}".format(e))
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
    #option_chain_info = option_chain_info_from_file.append(pd.DataFrame([option_chain_info]))
    data_frame = pd.concat([data_frame, pd.DataFrame([option_chain_info])], ignore_index=True)

    # Dump option chain data to a CSV file
    data_frame.to_csv(file_name, index=False)        

if __name__ == "__main__":
    main()
