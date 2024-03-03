"""Module providing a function for main function """

# pylint: disable=W1203
# pylint: disable=W1201
# pylint: disable=W1202
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105




from datetime import datetime
from enum import Enum
import logging
import time
import pandas as pd
import requests


logging.basicConfig(filename='/tmp/autostraddle.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

class UnderlyingSymbol(Enum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"
    FINNIFTY = "FINNIFTY"

# For nifty return 50, for bank nifty return 100, for finnifty return 50
def get_strike_interval(symbol):
    if symbol == "NIFTY":
        return 50
    elif symbol == "BANKNIFTY":
        return 100
    elif symbol == "FINNIFTY":
        return 50
    else:
        return 0

class OptionChainData:

    BASE_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={}"

    def __init__(self, symbol):
        self.symbol = symbol
        #self.url = self.BASE_URL.format(symbol.value)
        self.url = self.BASE_URL.format(symbol)

    def get_option_chain_info(self, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike):
        try:
            option_chain_data = self.get_option_chain_data_with_retry(self.url)

            # Extract CE and PE values as separate lists of dictionaries
            ce_values = [data['CE'] for data in option_chain_data['records']['data'] if
                         "CE" in data and data['expiryDate'].lower() == option_chain_data['records']['expiryDates'][
                             0].lower()]

            pe_values = [data['PE'] for data in option_chain_data['records']['data'] if
                         "PE" in data and data['expiryDate'].lower() == option_chain_data['records']['expiryDates'][
                             0].lower()]

            # Convert CE and PE lists to DataFrames
            df_ce = pd.DataFrame(ce_values)
            df_pe = pd.DataFrame(pe_values)

            spot_price = df_ce['underlyingValue'].iloc[0]

            # Find the ATM strike (nearest to spot price) for CE and PE
            atm_ce_strike = df_ce.loc[(df_ce['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']
            atm_pe_strike = df_pe.loc[(df_pe['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']

            # Create short dataframes which only 10 aboe and below of atm_ce_strike and atm_pe_strike
            df_ce_temp = df_ce[(df_ce['strikePrice'] >= atm_ce_strike - 10 * get_strike_interval(self.symbol)) \
                                & (df_ce['strikePrice'] <= atm_ce_strike + 10 * get_strike_interval(self.symbol))]
            df_pe_temp = df_pe[(df_pe['strikePrice'] >= atm_pe_strike - 10 * get_strike_interval(self.symbol)) \
                                & (df_pe['strikePrice'] <= atm_pe_strike + 10 * get_strike_interval(self.symbol))]

            # merge the two dataframes on strikePrice
            df_merge = pd.merge(df_ce_temp, df_pe_temp, on='strikePrice', suffixes=('_ce', '_pe'))

            #Get strike price which has minimium difference between lastPrice_ce and lastPrice_pe
            df_merge['diff'] = abs(df_merge['lastPrice_ce'] - df_merge['lastPrice_pe'])
            df_merge['diff'] = df_merge['diff'].astype(float)

            # Sort df_merge by diff
            df_merge = df_merge.sort_values(by=['diff'])

            # get strangle strike price which has minimium difference between lastPrice_ce and lastPrice_pe
            strangle_strike = df_merge['strikePrice'].iloc[0]

            # ce strangle strike price is 2 times of sum of lastPrice_ce and lastPrice_pe
            ce_strangle_strike = strangle_strike +  2 * ((df_merge['lastPrice_ce'] + df_merge['lastPrice_pe']).iloc[0])
            pe_strangle_strike = strangle_strike -  2 * ((df_merge['lastPrice_ce'] + df_merge['lastPrice_pe']).iloc[0])

            # round of ce_strangle_strike to nearest 50
            ce_strangle_strike = round(ce_strangle_strike / get_strike_interval(self.symbol)) * get_strike_interval(self.symbol)
            pe_strangle_strike = round(pe_strangle_strike / get_strike_interval(self.symbol)) * get_strike_interval(self.symbol)

            if prev_strangle_ce_strike == 0:
                prev_strangle_ce_strike = ce_strangle_strike

            if prev_strangle_pe_strike == 0:
                prev_strangle_pe_strike = pe_strangle_strike

            # Drop specified columns from df_ce and df_pe DataFrames
            columns_to_drop = ['expiryDate', 'underlying', 'underlyingValue', 'identifier', 'impliedVolatility',
                               'change',
                               'pChange',
                               'totalBuyQuantity', 'totalSellQuantity', 'bidQty', 'bidprice', 'askQty', 'askPrice',
                               'pchangeinOpenInterest']

            df_ce.drop(columns=columns_to_drop, inplace=True)
            df_pe.drop(columns=columns_to_drop, inplace=True)

            # Calculate the PE to CE ratio
            total_open_interest_ce = df_ce['openInterest'].sum()
            total_open_interest_pe = df_pe['openInterest'].sum()

            pe_to_ce_ratio = total_open_interest_pe / total_open_interest_ce

            # Find the last prices for ATM CE and ATM PE
            atm_ce_last_price = df_ce[df_ce['strikePrice'] == atm_ce_strike]['lastPrice'].values[0]
            atm_pe_last_price = df_pe[df_pe['strikePrice'] == atm_pe_strike]['lastPrice'].values[0]
            if prev_atm_strike == 0:
                prev_atm_ce_price = 0
                prev_atm_pe_price = 0
                prev_atm_next_ce_price = 0
                prev_atm_pe_strike_price = 0
            else:
                prev_atm_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike]['lastPrice'].values[0]
                prev_atm_pe_price = df_pe[df_pe['strikePrice'] == prev_atm_strike]['lastPrice'].values[0]
                prev_atm_next_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike + get_strike_interval(self.symbol)]['lastPrice'].values[0]
                prev_atm_pe_strike_price = df_pe[df_pe['strikePrice'] == prev_atm_strike - get_strike_interval(self.symbol)]['lastPrice'].values[0]

            # Save data to a dictionary along with the current time
            result_dict = {
                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'spot_price': spot_price,
                'pe_to_ce_ratio': pe_to_ce_ratio,
                'atm_strike': float(atm_ce_strike),
                'atm_current_ce_price': float(atm_ce_last_price),
                'atm_current_pe_price': float(atm_pe_last_price),
                'atm_next_ce_price': float(df_ce[df_ce['strikePrice'] == atm_ce_strike + get_strike_interval(self.symbol)]['lastPrice'].values[0]),
                'atm_next_pe_price': float(df_pe[df_pe['strikePrice'] == atm_pe_strike - get_strike_interval(self.symbol)]['lastPrice'].values[0]),
                'prev_atm_strike': prev_atm_strike,
                'prev_atm_ce_price': float(prev_atm_ce_price),
                'prev_atm_pe_price': float(prev_atm_pe_price),
                'prev_atm_next_ce_price': float(prev_atm_next_ce_price),
                'prev_atm_next_pe_price': float(prev_atm_pe_strike_price),
                'ce_strangle_strike': float(ce_strangle_strike),
                'pe_strangle_strike': float(pe_strangle_strike),
                'ce_strangle_price': float(df_ce[df_ce['strikePrice'] == ce_strangle_strike]['lastPrice'].values[0]),
                'pe_strangle_price': float(df_pe[df_pe['strikePrice'] == pe_strangle_strike]['lastPrice'].values[0]),
                'prev_strangle_ce_strike': prev_strangle_ce_strike,
                'prev_strangle_pe_strike': prev_strangle_pe_strike,
                'prev_ce_strangle_price': float(df_ce[df_ce['strikePrice'] == prev_strangle_ce_strike]['lastPrice'].values[0]),
                'prev_pe_strangle_price': float(df_pe[df_pe['strikePrice'] == prev_strangle_pe_strike]['lastPrice'].values[0])
            }

            return result_dict

        except Exception as e:
            print(f"Error: {e}")
            return None

    def get_option_chain_data_with_retry(self, url, max_retries=3, retry_delay=3):
        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        baseurl = "https://www.nseindia.com/"

        session = requests.Session()
        request = session.get(baseurl, headers=headers, timeout=5)
        cookies = dict(request.cookies)

        for retry in range(max_retries + 1):
            try:
                response = requests.get(url, headers=headers, cookies=cookies, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    return data
                else:
                    response.raise_for_status()  # Raise exception for non-200 status codes
            except requests.exceptions.RequestException as e:
                print(f"Request failed on retry {retry + 1}. Error: {e}")
                print(f"Request failed on retry {retry + 1}. Error: {url}")
                logging.error(f"Request failed on retry {retry + 1}. Error: {url}")
                if retry < max_retries:
                    print(f"Retrying after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error("Max retries exceeded. Unable to fetch data.")
                    raise requests.exceptions.RequestException("Max retries exceeded. Unable to fetch data.") from e

    def extract_top_open_interest_values(self, df, top_n=3):
        df_with_open_interest = df[df['openInterest'] > 0]
        return df_with_open_interest.nlargest(top_n, 'openInterest')

"""
# Example usage:
symbol = UnderlyingSymbol.NIFTY
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(21600, 0, 0)

# You can then access the information using option_chain_info
print(option_chain_info)


symbol = UnderlyingSymbol.BANKNIFTY
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0)

# You can then access the information using option_chain_info
print(option_chain_info)


symbol = UnderlyingSymbol.FINNIFTY
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(0,0,0)

# You can then access the information using option_chain_info
print(option_chain_info)

"""