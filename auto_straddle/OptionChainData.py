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
import json
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class UnderlyingSymbol(Enum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"
    FINNIFTY = "FINNIFTY"
    MIDCPNIFTY = "MIDCPNIFTY"

# For nifty return 50, for bank nifty return 100, for finnifty return 50
def get_strike_interval(symbolsearch):
    if symbolsearch == "NIFTY":
        return 50
    if symbolsearch == "BANKNIFTY":
        return 100
    if symbolsearch == "FINNIFTY":
        return 50
    if symbolsearch == "MIDCPNIFTY":
        return 25
    if symbolsearch == "SENSEX":
        return 100
    return 0

class OptionChainData:

    BASE_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={}"

    get_from = "groww"

    def __init__(self, symbolinit):
        self.symbol = symbolinit
        #self.url = self.BASE_URL.format(symbol.value)
        self.url = self.BASE_URL.format(symbolinit)
        self.bse_expiry_date_pd = None

    def get_option_chain_info(self, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData):
        if self.get_from == "groww":
            try :
                ret = self.extract_options_data_groww(prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData)
            except Exception as e:
                print(f"Error: {e}")
                ret = None
            if ret is None:
                if symbolData == "SENSEX":
                    return self.get_option_chain_data_bse(prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData)
                return self.get_option_chain_info_nse(prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData)
            else:
                return ret
        else:
            return self.get_option_chain_info_nse(prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData)

    def get_option_chain_info_nse(self, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData):
        try:
            # First, get the expiry dates
            contract_info_url = f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbolData}"
            contract_info_data = self.get_option_chain_data_with_retry(contract_info_url)

            if not contract_info_data or 'expiryDates' not in contract_info_data:
                print(f"Failed to get expiry dates for {symbolData}")
                return None

            expiry_dates = contract_info_data['expiryDates']
            if not expiry_dates:
                print(f"No expiry dates found for {symbolData}")
                return None

            # Use the first expiry date
            first_expiry = expiry_dates[0]
            print(f"Using expiry date: {first_expiry}")

            # Now get the option chain data for the first expiry
            option_chain_url = f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={symbolData}&expiry={first_expiry}"
            option_chain_data = self.get_option_chain_data_with_retry(option_chain_url)

            if not option_chain_data or 'records' not in option_chain_data:
                print(f"Failed to get option chain data for {symbolData}")
                return None

            # Extract CE and PE values as separate lists of dictionaries
            ce_values = [data['CE'] for data in option_chain_data['records']['data'] if "CE" in data]
            pe_values = [data['PE'] for data in option_chain_data['records']['data'] if "PE" in data]

            # Convert CE and PE lists to DataFrames
            df_ce = pd.DataFrame(ce_values)
            df_pe = pd.DataFrame(pe_values)

            if df_ce.empty:
                print(f"No CE options data found for {symbolData}")
                return None

            spot_price = df_ce['underlyingValue'].iloc[0] if 'underlyingValue' in df_ce.columns and not df_ce.empty else 0

            # Find the ATM strike (nearest to spot price) for CE and PE
            atm_ce_strike = df_ce.loc[(df_ce['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']
            atm_pe_strike = df_pe.loc[(df_pe['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']

            # Create short dataframes which only 10 above and below of atm_ce_strike and atm_pe_strike
            df_ce_temp = df_ce[(df_ce['strikePrice'] >= atm_ce_strike - 10 * get_strike_interval(symbolData)) \
                                & (df_ce['strikePrice'] <= atm_ce_strike + 10 * get_strike_interval(symbolData))]
            df_pe_temp = df_pe[(df_pe['strikePrice'] >= atm_pe_strike - 10 * get_strike_interval(symbolData)) \
                                & (df_pe['strikePrice'] <= atm_pe_strike + 10 * get_strike_interval(symbolData))]

            # merge the two dataframes on strikePrice
            df_merge = pd.merge(df_ce_temp, df_pe_temp, on='strikePrice', suffixes=('_ce', '_pe'))

            df_merge_temp = df_merge.iloc[5:]
            df_merge_temp = df_merge_temp[:-5]

            #Get strike price which has minimum difference between lastPrice_ce and lastPrice_pe
            df_merge_temp['diff'] = abs(df_merge_temp['lastPrice_ce'] - df_merge_temp['lastPrice_pe'])
            df_merge_temp['diff'] = df_merge_temp['diff'].astype(float)

            # Sort df_merge by diff
            df_merge_temp = df_merge_temp.sort_values(by=['diff'])

            # get strangle strike price which has minimum difference between lastPrice_ce and lastPrice_pe
            strangle_strike = df_merge_temp['strikePrice'].iloc[0]

            atm_ce_strike = strangle_strike
            atm_pe_strike = strangle_strike

            # ce strangle strike price is 2 times of sum of lastPrice_ce and lastPrice_pe
            ce_strangle_strike = strangle_strike +  2 * ((df_merge_temp['lastPrice_ce'] + df_merge_temp['lastPrice_pe']).iloc[0])
            pe_strangle_strike = strangle_strike -  2 * ((df_merge_temp['lastPrice_ce'] + df_merge_temp['lastPrice_pe']).iloc[0])

            # round of ce_strangle_strike to nearest strike interval
            ce_strangle_strike = round(ce_strangle_strike / get_strike_interval(symbolData)) * get_strike_interval(symbolData)
            pe_strangle_strike = round(pe_strangle_strike / get_strike_interval(symbolData)) * get_strike_interval(symbolData)

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

            # Only drop columns that exist in the DataFrames
            columns_to_drop_ce = [col for col in columns_to_drop if col in df_ce.columns]
            columns_to_drop_pe = [col for col in columns_to_drop if col in df_pe.columns]

            df_ce.drop(columns=columns_to_drop_ce, inplace=True)
            df_pe.drop(columns=columns_to_drop_pe, inplace=True)

            # Get the rows with the highest, second highest, and third highest openInterest in df_ce and df_pe
            ce_rows_sorted_by_open_interest = self.extract_top_open_interest_values(df_ce)
            pe_rows_sorted_by_open_interest = self.extract_top_open_interest_values(df_pe)

            # Extract values of strikePrice, openInterest, and lastPrice from the rows with the highest, second highest, and third highest openInterest
            ce_highest_values = ce_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'openInterest', 'lastPrice']].values
            ce_second_highest_values = ce_rows_sorted_by_open_interest.iloc[1][
                ['strikePrice', 'openInterest', 'lastPrice']].values
            ce_third_highest_values = ce_rows_sorted_by_open_interest.iloc[2][
                ['strikePrice', 'openInterest', 'lastPrice']].values

            pe_highest_values = pe_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'openInterest', 'lastPrice']].values
            pe_second_highest_values = pe_rows_sorted_by_open_interest.iloc[1][
                ['strikePrice', 'openInterest', 'lastPrice']].values
            pe_third_highest_values = pe_rows_sorted_by_open_interest.iloc[2][
                ['strikePrice', 'openInterest', 'lastPrice']].values

            # Calculate the PE to CE ratio
            total_open_interest_ce = df_ce['openInterest'].sum()
            total_open_interest_pe = df_pe['openInterest'].sum()

            if total_open_interest_ce > 0:
                pe_to_ce_ratio = total_open_interest_pe / total_open_interest_ce
            else:
                print("Warning: CE open interest is zero, using default ratio value")
                pe_to_ce_ratio = 0  # or some other default value

            # Find the last prices for ATM CE and ATM PE
            atm_ce_last_price = df_ce[df_ce['strikePrice'] == atm_ce_strike]['lastPrice'].values[0]
            atm_pe_last_price = df_pe[df_pe['strikePrice'] == atm_pe_strike]['lastPrice'].values[0]
            if prev_atm_strike == 0:
                prev_atm_ce_price = 0
                prev_atm_pe_price = 0
                prev_atm_next_ce_price = 0
                prev_atm_pe_strike_price = 0
            else:
                prev_atm_ce_price = self.safe_get_dataframe_value(
                    df_ce,
                    df_ce['strikePrice'] == prev_atm_strike,
                    'lastPrice',
                    default_value=0
                )
                prev_atm_pe_price = df_pe[df_pe['strikePrice'] == prev_atm_strike]['lastPrice'].values[0]
                prev_atm_next_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike + (2 * get_strike_interval(symbolData))]['lastPrice'].values[0]
                prev_atm_pe_strike_price = df_pe[df_pe['strikePrice'] == prev_atm_strike - (2 * get_strike_interval(symbolData))]['lastPrice'].values[0]

            # Save data to a dictionary along with the current time
            result_dict = {
                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'spot_price': spot_price,
                'pe_to_ce_ratio': pe_to_ce_ratio,
                'atm_strike': float(atm_ce_strike),
                'atm_current_ce_price': float(atm_ce_last_price),
                'atm_current_pe_price': float(atm_pe_last_price),
                'atm_next_ce_price': float(df_ce[df_ce['strikePrice'] == atm_ce_strike + (2 * get_strike_interval(symbolData))]['lastPrice'].values[0]),
                'atm_next_pe_price': float(df_pe[df_pe['strikePrice'] == atm_pe_strike - (2 * get_strike_interval(symbolData))]['lastPrice'].values[0]),
                'prev_atm_strike': prev_atm_strike,
                'prev_atm_ce_price': float(prev_atm_ce_price),
                'prev_atm_pe_price': float(prev_atm_pe_price),
                'prev_atm_next_ce_price': float(prev_atm_next_ce_price),
                'prev_atm_next_pe_price': float(prev_atm_pe_strike_price),
                'ce_strangle_strike': float(ce_strangle_strike),
                'pe_strangle_strike': float(pe_strangle_strike),
                'ce_strangle_price': float(df_ce[df_ce['strikePrice'] == ce_strangle_strike]['lastPrice'].values[0]) if not df_ce[df_ce['strikePrice'] == ce_strangle_strike].empty else 0,
                'pe_strangle_price': float(df_pe[df_pe['strikePrice'] == pe_strangle_strike]['lastPrice'].values[0]),
                'prev_strangle_ce_strike': prev_strangle_ce_strike,
                'prev_strangle_pe_strike': prev_strangle_pe_strike,
                'prev_ce_strangle_price': float(df_ce[df_ce['strikePrice'] == prev_strangle_ce_strike]['lastPrice'].values[0]),
                'prev_pe_strangle_price': float(df_pe[df_pe['strikePrice'] == prev_strangle_pe_strike]['lastPrice'].values[0]),
                'ce_highest_strike': float(ce_highest_values[0]),
                'ce_highest_open_interest': float(ce_highest_values[1]),
                'ce_highest_last_price': float(ce_highest_values[2]),
                'pe_highest_strike': float(pe_highest_values[0]),
                'pe_highest_open_interest': float(pe_highest_values[1]),
                'pe_highest_last_price': float(pe_highest_values[2]),
                'ce_second_highest_strike': float(ce_second_highest_values[0]),
                'ce_second_highest_open_interest': float(ce_second_highest_values[1]),
                'ce_second_highest_last_price': float(ce_second_highest_values[2]),
                'pe_second_highest_strike': float(pe_second_highest_values[0]),
                'pe_second_highest_open_interest': float(pe_second_highest_values[1]),
                'pe_second_highest_last_price': float(pe_second_highest_values[2]),
                'ce_third_highest_strike': float(ce_third_highest_values[0]),
                'ce_third_highest_open_interest': float(ce_third_highest_values[1]),
                'ce_third_highest_last_price': float(ce_third_highest_values[2]),
                'pe_third_highest_strike': float(pe_third_highest_values[0]),
                'pe_third_highest_open_interest': float(pe_third_highest_values[1]),
                'pe_third_highest_last_price': float(pe_third_highest_values[2])
            }

            return result_dict

        except Exception as e:
            print(f"Error: {e}")
            return None

    def get_option_chain_data_with_retry(self, url, max_retries=3, retry_delay=3):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none"
        }

        baseurl = "https://www.nseindia.com/"

        with requests.Session() as session:
            # First, visit the base URL to establish session and get cookies
            try:
                session.headers.update(headers)
                request = session.get(baseurl, headers=headers, timeout=10)
                request.raise_for_status()
                cookies = dict(request.cookies)
                print(f"Session established, got {len(cookies)} cookies")
            except Exception as e:
                print(f"Failed to establish session: {e}")
                return None

            for retry in range(max_retries + 1):
                try:
                    # Update headers for API call
                    api_headers = headers.copy()
                    api_headers.update({
                        "Referer": "https://www.nseindia.com/option-chain",
                        "Host": "www.nseindia.com",
                        "X-Requested-With": "XMLHttpRequest"
                    })

                    response = session.get(url, headers=api_headers, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        return data
                    else:
                        print(f"HTTP {response.status_code}: {response.reason}")
                        response.raise_for_status()
                except requests.exceptions.RequestException as e:
                    print(f"Request failed on retry {retry + 1}. Error: {e}")
                    logging.error(f"Request failed on retry {retry + 1}. Error: {url}")
                    if retry < max_retries:
                        print(f"Retrying after {retry_delay} seconds...")
                        time.sleep(retry_delay)
                    else:
                        logging.error("Max retries exceeded. Unable to fetch data.")
                        raise requests.exceptions.RequestException("Max retries exceeded.") from e

    def extract_top_open_interest_values(self, df, top_n=3):
        df_with_open_interest = df[df['openInterest'] > 0]
        return df_with_open_interest.nlargest(top_n, 'openInterest')

    def extract_top_open_interest_values_ce(self, df, top_n=3):
        df_with_open_interest = df[df['call_open_interest'] > 0]
        return df_with_open_interest.nlargest(top_n, 'call_open_interest')

    def extract_top_open_interest_values_pe(self, df, top_n=3):
        df_with_open_interest = df[df['put_open_interest'] > 0]
        return df_with_open_interest.nlargest(top_n, 'put_open_interest')

    def parse_json_groww(self, data, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData):
        print("Parsing JSON data from Groww...")

        # Extract spot price from the new JSON structure
        try:
            spot_price = data.get('props', {}).get('pageProps', {}).get('data', {}).get('company', {}).get('liveData', {}).get('ltp')

            if spot_price is None:
                print("Could not extract spot price from data")
                return None

            spot_price = float(spot_price)
            print(f"Extracted spot price: {spot_price}")

        except Exception as e:
            print(f"Error extracting spot price: {e}")
            return None

        # Build result dictionary for option chain data
        result_dict = {}
        option_contracts = data.get('props', {}).get('pageProps', {}).get('data', {}).get('optionChain', {}).get('optionContracts', [])

        if not option_contracts:
            print("No option contracts found in data")
            return None

        for option in option_contracts:
            strike_price = option.get('strikePrice')
            if strike_price is None:
                continue

            # Extract call and put data from the new structure
            ce_data = option.get('ce', {})
            pe_data = option.get('pe', {})

            # Extract live data for calls and puts
            call_live_data = ce_data.get('liveData', {})
            put_live_data = pe_data.get('liveData', {})

            call_ltp = call_live_data.get('ltp', 0)
            put_ltp = put_live_data.get('ltp', 0)
            call_open_interest = call_live_data.get('oi', 0)
            put_open_interest = put_live_data.get('oi', 0)

            # Convert strike price from paise to rupees (divide by 100)
            strike_price = float(strike_price) / 100

            result_dict[strike_price] = {
                'call_ltp': call_ltp, 
                'put_ltp': put_ltp,
                'call_open_interest': call_open_interest,
                'put_open_interest': put_open_interest
            }

        if not result_dict:
            print("No option data extracted from contracts")
            return None

        # Convert to DataFrame format (same as before)
        ce_list = []
        pe_list = []
        for strike_price, option_data in result_dict.items():
            ce_list.append({
                'strikePrice': float(strike_price),
                'call_ltp': float(option_data['call_ltp']) if option_data['call_ltp'] else 0,
                'call_open_interest': int(option_data['call_open_interest']) if option_data['call_open_interest'] else 0
            })
            pe_list.append({
                'strikePrice': float(strike_price),
                'put_ltp': float(option_data['put_ltp']) if option_data['put_ltp'] else 0,
                'put_open_interest': int(option_data['put_open_interest']) if option_data['put_open_interest'] else 0
            })

        df_ce = pd.DataFrame(ce_list)
        df_pe = pd.DataFrame(pe_list)

        if df_ce.empty or df_pe.empty:
            print("Empty DataFrames created from option data")
            return None

        # Get the rows with the highest, second highest, and third highest openInterest in df_ce and df_pe
        ce_rows_sorted_by_open_interest = self.extract_top_open_interest_values_ce(df_ce)
        pe_rows_sorted_by_open_interest = self.extract_top_open_interest_values_pe(df_pe)

        # Extract values of strikePrice, openInterest, and lastPrice from the rows with the highest, second highest, and third highest openInterest
        if len(ce_rows_sorted_by_open_interest) >= 3:
            ce_highest_values = ce_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'call_open_interest', 'call_ltp']].values
            ce_second_highest_values = ce_rows_sorted_by_open_interest.iloc[1][['strikePrice', 'call_open_interest', 'call_ltp']].values
            ce_third_highest_values = ce_rows_sorted_by_open_interest.iloc[2][['strikePrice', 'call_open_interest', 'call_ltp']].values
        else:
            print("Not enough CE data for top OI calculation")
            return None

        if len(pe_rows_sorted_by_open_interest) >= 3:
            pe_highest_values = pe_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'put_open_interest', 'put_ltp']].values
            pe_second_highest_values = pe_rows_sorted_by_open_interest.iloc[1][['strikePrice', 'put_open_interest', 'put_ltp']].values
            pe_third_highest_values = pe_rows_sorted_by_open_interest.iloc[2][['strikePrice', 'put_open_interest', 'put_ltp']].values
        else:
            print("Not enough PE data for top OI calculation")
            return None

        # Find the ATM strike (nearest to spot price) for CE and PE
        atm_ce_strike = df_ce.loc[(df_ce['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']
        atm_pe_strike = df_pe.loc[(df_pe['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']

        # Create short dataframes which only 10 above and below of atm_ce_strike and atm_pe_strike
        strike_interval = get_strike_interval(symbolData)
        df_ce_temp = df_ce[(df_ce['strikePrice'] >= atm_ce_strike - 10 * strike_interval) &
                           (df_ce['strikePrice'] <= atm_ce_strike + 10 * strike_interval)]
        df_pe_temp = df_pe[(df_pe['strikePrice'] >= atm_pe_strike - 10 * strike_interval) &
                           (df_pe['strikePrice'] <= atm_pe_strike + 10 * strike_interval)]

        # Merge the two dataframes on strikePrice
        df_merge = pd.merge(df_ce_temp, df_pe_temp, on='strikePrice', suffixes=('_ce', '_pe'))

        if len(df_merge) < 11:  # Need at least 11 rows to slice properly
            print("Insufficient data for strangle calculation")
            return None

        df_merge_temp = df_merge.iloc[5:-5].copy()  # Remove first 5 and last 5

        # Get strike price which has minimum difference between call_ltp and put_ltp
        df_merge_temp['diff'] = abs(df_merge_temp['call_ltp'] - df_merge_temp['put_ltp'])
        df_merge_temp['diff'] = df_merge_temp['diff'].astype(float)

        # Sort df_merge by diff
        df_merge_temp = df_merge_temp.sort_values(by=['diff'])

        # Get strangle strike price which has minimum difference between call_ltp and put_ltp
        strangle_strike = df_merge_temp['strikePrice'].iloc[0]

        atm_ce_strike = strangle_strike
        atm_pe_strike = strangle_strike

        # CE strangle strike price is 2 times of sum of call_ltp and put_ltp
        straddle_sum = df_merge_temp['call_ltp'].iloc[0] + df_merge_temp['put_ltp'].iloc[0]
        ce_strangle_strike = strangle_strike + 2 * straddle_sum
        pe_strangle_strike = strangle_strike - 2 * straddle_sum

        # Round to nearest strike interval
        ce_strangle_strike = round(ce_strangle_strike / strike_interval) * strike_interval
        pe_strangle_strike = round(pe_strangle_strike / strike_interval) * strike_interval

        # Apply symbol-specific rounding rules
        if symbolData in ["MIDCPNIFTY", "FINNIFTY"]:
            remainder = ce_strangle_strike % 100
            ce_strangle_strike = ce_strangle_strike - remainder
            remainder = pe_strangle_strike % 100
            pe_strangle_strike = pe_strangle_strike - remainder

        if symbolData == "BANKNIFTY":
            remainder = ce_strangle_strike % 500
            ce_strangle_strike = ce_strangle_strike - remainder
            remainder = pe_strangle_strike % 500
            pe_strangle_strike = pe_strangle_strike - remainder

        if prev_strangle_ce_strike == 0:
            prev_strangle_ce_strike = ce_strangle_strike

        if prev_strangle_pe_strike == 0:
            prev_strangle_pe_strike = pe_strangle_strike

        # Calculate the PE to CE ratio
        total_open_interest_ce = df_ce['call_open_interest'].sum()
        total_open_interest_pe = df_pe['put_open_interest'].sum()

        pe_to_ce_ratio = total_open_interest_pe / max(total_open_interest_ce, 1)  # Avoid division by zero

        # Find the last prices for ATM CE and ATM PE
        atm_ce_last_price = df_ce[df_ce['strikePrice'] == atm_ce_strike]['call_ltp'].iloc[0]
        atm_pe_last_price = df_pe[df_pe['strikePrice'] == atm_pe_strike]['put_ltp'].iloc[0]

        if prev_atm_strike == 0:
            prev_atm_ce_price = 0
            prev_atm_pe_price = 0
            prev_atm_next_ce_price = 0
            prev_atm_pe_strike_price = 0
        else:
            prev_atm_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike]['call_ltp'].iloc[0] if not df_ce[df_ce['strikePrice'] == prev_atm_strike].empty else 0
            prev_atm_pe_price = df_pe[df_pe['strikePrice'] == prev_atm_strike]['put_ltp'].iloc[0] if not df_pe[df_pe['strikePrice'] == prev_atm_strike].empty else 0
            prev_atm_next_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike + (2 * strike_interval)]['call_ltp'].iloc[0] if not df_ce[df_ce['strikePrice'] == prev_atm_strike + (2 * strike_interval)].empty else 0
            prev_atm_pe_strike_price = df_pe[df_pe['strikePrice'] == prev_atm_strike - (2 * strike_interval)]['put_ltp'].iloc[0] if not df_pe[df_pe['strikePrice'] == prev_atm_strike - (2 * strike_interval)].empty else 0

        # Save data to a dictionary along with the current time
        result_dict = {
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'spot_price': spot_price,
            'pe_to_ce_ratio': pe_to_ce_ratio,
            'atm_strike': float(atm_ce_strike),
            'atm_current_ce_price': float(atm_ce_last_price),
            'atm_current_pe_price': float(atm_pe_last_price),
            'atm_next_ce_price': float(df_ce[df_ce['strikePrice'] == atm_ce_strike + (2 * strike_interval)]['call_ltp'].iloc[0]) if not df_ce[df_ce['strikePrice'] == atm_ce_strike + (2 * strike_interval)].empty else 0,
            'atm_next_pe_price': float(df_pe[df_pe['strikePrice'] == atm_pe_strike - (2 * strike_interval)]['put_ltp'].iloc[0]) if not df_pe[df_pe['strikePrice'] == atm_pe_strike - (2 * strike_interval)].empty else 0,
            'prev_atm_strike': prev_atm_strike,
            'prev_atm_ce_price': float(prev_atm_ce_price),
            'prev_atm_pe_price': float(prev_atm_pe_price),
            'prev_atm_next_ce_price': float(prev_atm_next_ce_price),
            'prev_atm_next_pe_price': float(prev_atm_pe_strike_price),
            'ce_strangle_strike': float(ce_strangle_strike),
            'pe_strangle_strike': float(pe_strangle_strike),
            'ce_strangle_price': float(df_ce[df_ce['strikePrice'] == ce_strangle_strike]['call_ltp'].iloc[0]) if not df_ce[df_ce['strikePrice'] == ce_strangle_strike].empty else 0,
            'pe_strangle_price': float(df_pe[df_pe['strikePrice'] == pe_strangle_strike]['put_ltp'].iloc[0]) if not df_pe[df_pe['strikePrice'] == pe_strangle_strike].empty else 0,
            'prev_strangle_ce_strike': prev_strangle_ce_strike,
            'prev_strangle_pe_strike': prev_strangle_pe_strike,
            'prev_ce_strangle_price': float(df_ce[df_ce['strikePrice'] == prev_strangle_ce_strike]['call_ltp'].iloc[0]) if not df_ce[df_ce['strikePrice'] == prev_strangle_ce_strike].empty else 0,
            'prev_pe_strangle_price': float(df_pe[df_pe['strikePrice'] == prev_strangle_pe_strike]['put_ltp'].iloc[0]) if not df_pe[df_pe['strikePrice'] == prev_strangle_pe_strike].empty else 0,
            'ce_highest_strike': float(ce_highest_values[0]),
            'ce_highest_open_interest': float(ce_highest_values[1]),
            'ce_highest_last_price': float(ce_highest_values[2]),
            'pe_highest_strike': float(pe_highest_values[0]),
            'pe_highest_open_interest': float(pe_highest_values[1]),
            'pe_highest_last_price': float(pe_highest_values[2]),
            'ce_second_highest_strike': float(ce_second_highest_values[0]),
            'ce_second_highest_open_interest': float(ce_second_highest_values[1]),
            'ce_second_highest_last_price': float(ce_second_highest_values[2]),
            'pe_second_highest_strike': float(pe_second_highest_values[0]),
            'pe_second_highest_open_interest': float(pe_second_highest_values[1]),
            'pe_second_highest_last_price': float(pe_second_highest_values[2]),
            'ce_third_highest_strike': float(ce_third_highest_values[0]),
            'ce_third_highest_open_interest': float(ce_third_highest_values[1]),
            'ce_third_highest_last_price': float(ce_third_highest_values[2]),
            'pe_third_highest_strike': float(pe_third_highest_values[0]),
            'pe_third_highest_open_interest': float(pe_third_highest_values[1]),
            'pe_third_highest_last_price': float(pe_third_highest_values[2])
        }

        return result_dict

    def extract_options_data_groww(self, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData):
        max_retries=2
        retry_delay=1
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        if symbolData == "NIFTY":
            url = "https://groww.in/options/nifty"
        elif symbolData == "BANKNIFTY":
            url = "https://groww.in/options/nifty-bank"
        elif symbolData == "FINNIFTY":
            url = "https://groww.in/options/nifty-financial-services"
        elif symbolData == "MIDCPNIFTY":
            url = "https://groww.in/options/nifty-midcap-select"
        elif symbolData == "SENSEX":
            url = "https://groww.in/options/sp-bse-sensex"
        else:
            return None

        baseurl = "https://groww.in/"

        session = requests.Session()
        request = session.get(baseurl, headers=headers, timeout=5)
        cookies = dict(request.cookies)

        for retry in range(max_retries + 1):
            try:
                response = requests.get(url, headers=headers, cookies=cookies, timeout=5)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    script_tag = soup.find('script', {'id': '__NEXT_DATA__'})
                    if script_tag:
                        json_text = script_tag.string
                        data = json.loads(json_text)
                        #print(data)
                        result = self.parse_json_groww(data, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData)
                        return result
                    else:
                        response.raise_for_status()  # Raise exception for non-200 status codes
            except requests.exceptions.RequestException as e:
                print("Script tag with id '__NEXT_DATA__' not found.")
                print(f"Request failed on retry {retry + 1}. Error: {e}")
                print(f"Request failed on retry {retry + 1}. Error: {url}")
                logging.error(f"Request failed on retry {retry + 1}. Error: {url}")
                if retry < max_retries:
                    print(f"Retrying after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logging.error("Max retries exceeded. Unable to fetch data.")
                    return None

    def safe_get_dataframe_value(self, df, condition, column, default_value=0):
        """Safely access DataFrame values with validation"""
        matching_rows = df[condition]
        if not matching_rows.empty and column in matching_rows.columns:
            return matching_rows[column].values[0]
        return default_value


    def get_option_chain_data_bse(self, expiry_date, session, scrip_cd=1, strike_price=0):
        """
        Get option chain data from BSE API

        Args:
            expiry_date: Expiry date in format "24 Jun 2025"
            scrip_cd: Script code (default: 1 for SENSEX)
            strike_price: Strike price (default: 0 for all strikes)
        """
        try:
            # First, initialize the session
            url = "https://www.bseindia.com/markets/Derivatives/DeriReports/DeriOptionchain.html"

            response = session.get(url, timeout=30)
            response.raise_for_status()

            # Extract any necessary cookies or session tokens if needed
            _ = session.cookies.get_dict()

            # Now make the API call using the established session
            api_url = "https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain_IV/w"

            params = {
                'Expiry': expiry_date,
                'scrip_cd': scrip_cd,
                'strprice': strike_price
            }

            # Update headers for API call
            api_headers = {
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://www.bseindia.com/',
                'X-Requested-With': 'XMLHttpRequest'
            }

            # Update session headers for API call
            session.headers.update(api_headers)

            response = session.get(api_url, params=params, timeout=30)
            response.raise_for_status()

            # Parse JSON response
            data = response.json()
            return data

        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            return None
        except ValueError as e:
            print(f"JSON parsing error: {e}")
            return None

    def set_bse_expiry_date_pd(self, expiry_date):
        self.bse_expiry_date_pd = expiry_date

    def extract_options_data_bse(self, prev_atm_strike, prev_strangle_ce_strike, prev_strangle_pe_strike, symbolData):
        session = requests.Session()

        # Set up retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set proper headers to mimic Mozilla browser
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        })


        url = "https://www.bseindia.com/markets/Derivatives/DeriReports/DeriOptionchain.html"

        response = session.get(url, timeout=30)
        response.raise_for_status()

        # Extract any necessary cookies or session tokens if needed
        _ = session.cookies.get_dict()

        # Get option chain data for SENSEX expiry on 24 Jun 2025
        data = self.get_option_chain_data_bse(self.bse_expiry_date_pd.strftime('%d %b %Y'), session, scrip_cd=1, strike_price=0)

        if data:
            option_chain = data.get("Table", [])

            first_entry = option_chain[0]
            spot_price = first_entry.get("UlaValue", 0)

            # convert spot_price to float
            spot_price = float(spot_price.replace(',', '')) if isinstance(spot_price, str) else float(spot_price)

            # Step 5: Parse the required fields
            pe_parsed_data = []
            for entry in option_chain:
                try:
                    pe_parsed_data.append({
                        'strikePrice': float(entry.get('Strike_Price', '0').replace(',', '')),
                        'put_open_interest': int(entry.get('Open_Interest', '0')),
                        'put_ltp': float(entry.get('Last_Trd_Price', '0'))
                    })
                except (ValueError, TypeError, AttributeError):
                    pass

            # Step 6: Create a DataFrame
            df_pe = pd.DataFrame(pe_parsed_data)

            #Convert strikePrice column to numeric
            df_pe['strikePrice'] = pd.to_numeric(df_pe['strikePrice'], errors='coerce')

            # Step 5: Parse the required fields
            ce_parsed_data = []
            for entry in option_chain:
                try:
                    ce_parsed_data.append({
                        'strikePrice': float(entry.get('Strike_Price', '0').replace(',', '')),
                        'call_open_interest': int(entry.get('Open_Interest', '0')),
                        'call_ltp': float(entry.get('C_Last_Trd_Price', '0'))
                    })
                except (ValueError, TypeError, AttributeError):
                    pass

            # Step 6: Create a DataFrame
            df_ce = pd.DataFrame(ce_parsed_data)
            df_ce['strikePrice'] = pd.to_numeric(df_ce['strikePrice'], errors='coerce')

            # Get the rows with the highest, second highest, and third highest openInterest in df_ce and df_pe
            ce_rows_sorted_by_open_interest = self.extract_top_open_interest_values_ce(df_ce)
            pe_rows_sorted_by_open_interest = self.extract_top_open_interest_values_pe(df_pe)

            # Extract values of strikePrice, openInterest, and lastPrice from the rows with the highest, second highest, and third highest openInterest
            ce_highest_values = ce_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'call_open_interest', 'call_ltp']].values
            ce_second_highest_values = ce_rows_sorted_by_open_interest.iloc[1][
                ['strikePrice', 'call_open_interest', 'call_ltp']].values
            ce_third_highest_values = ce_rows_sorted_by_open_interest.iloc[2][
                ['strikePrice', 'call_open_interest', 'call_ltp']].values

            pe_highest_values = pe_rows_sorted_by_open_interest.iloc[0][['strikePrice', 'put_open_interest', 'put_ltp']].values
            pe_second_highest_values = pe_rows_sorted_by_open_interest.iloc[1][
                ['strikePrice', 'put_open_interest', 'put_ltp']].values
            pe_third_highest_values = pe_rows_sorted_by_open_interest.iloc[2][
                ['strikePrice', 'put_open_interest', 'put_ltp']].values

            # Find the ATM strike (nearest to spot price) for CE and PE
            atm_ce_strike = df_ce.loc[(df_ce['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']
            atm_pe_strike = df_pe.loc[(df_pe['strikePrice'] - spot_price).abs().idxmin()]['strikePrice']

            # Create short dataframes which only 10 aboe and below of atm_ce_strike and atm_pe_strike
            df_ce_temp = df_ce[(df_ce['strikePrice'] >= atm_ce_strike - 10 * get_strike_interval(symbolData)) \
                                & (df_ce['strikePrice'] <= atm_ce_strike + 10 * get_strike_interval(symbolData))]
            df_pe_temp = df_pe[(df_pe['strikePrice'] >= atm_pe_strike - 10 * get_strike_interval(symbolData)) \
                                & (df_pe['strikePrice'] <= atm_pe_strike + 10 * get_strike_interval(symbolData))]

            # merge the two dataframes on strikePrice
            df_merge = pd.merge(df_ce_temp, df_pe_temp, on='strikePrice', suffixes=('_ce', '_pe'))

            df_merge_temp = df_merge.iloc[5:]
            df_merge_temp = df_merge_temp[:-5]

            #Get strike price which has minimium difference between lastPrice_ce and lastPrice_pe
            df_merge_temp['diff'] = abs(df_merge_temp['call_ltp'] - df_merge_temp['put_ltp'])
            df_merge_temp['diff'] = df_merge_temp['diff'].astype(float)

            # Sort df_merge by diff
            df_merge_temp = df_merge_temp.sort_values(by=['diff'])

            # get strangle strike price which has minimium difference between lastPrice_ce and lastPrice_pe
            strangle_strike = df_merge_temp['strikePrice'].iloc[0]

            atm_ce_strike = strangle_strike
            atm_pe_strike = strangle_strike

            #print(symbolData, strangle_strike, df_merge_temp['call_ltp'].iloc[0], df_merge_temp['put_ltp'].iloc[0])

            # ce strangle strike price is 2 times of sum of lastPrice_ce and lastPrice_pe
            ce_strangle_strike = strangle_strike +  2 * ((df_merge_temp['call_ltp'] + df_merge_temp['put_ltp']).iloc[0])
            pe_strangle_strike = strangle_strike -  2 * ((df_merge_temp['call_ltp'] + df_merge_temp['put_ltp']).iloc[0])

            #print(strangle_strike, ce_strangle_strike, pe_strangle_strike)

            # round of ce_strangle_strike to nearest 50
            ce_strangle_strike = round(ce_strangle_strike / get_strike_interval(symbolData)) * get_strike_interval(symbolData)
            pe_strangle_strike = round(pe_strangle_strike / get_strike_interval(symbolData)) * get_strike_interval(symbolData)

            #print(ce_strangle_strike, pe_strangle_strike)

            if prev_strangle_ce_strike == 0:
                prev_strangle_ce_strike = ce_strangle_strike

            if prev_strangle_pe_strike == 0:
                prev_strangle_pe_strike = pe_strangle_strike

            # Calculate the PE to CE ratio
            total_open_interest_ce = df_ce['call_open_interest'].sum()
            total_open_interest_pe = df_pe['put_open_interest'].sum()

            pe_to_ce_ratio = total_open_interest_pe / total_open_interest_ce

            # Find the last prices for ATM CE and ATM PE
            atm_ce_last_price = df_ce[df_ce['strikePrice'] == atm_ce_strike]['call_ltp'].values[0]
            atm_pe_last_price = df_pe[df_pe['strikePrice'] == atm_pe_strike]['put_ltp'].values[0]
            if prev_atm_strike == 0:
                prev_atm_ce_price = 0
                prev_atm_pe_price = 0
                prev_atm_next_ce_price = 0
                prev_atm_pe_strike_price = 0
            else:
                prev_atm_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike]['call_ltp'].values[0]
                prev_atm_pe_price = df_pe[df_pe['strikePrice'] == prev_atm_strike]['put_ltp'].values[0]
                prev_atm_next_ce_price = df_ce[df_ce['strikePrice'] == prev_atm_strike + (2 * get_strike_interval(symbolData))]['call_ltp'].values[0]
                prev_atm_pe_strike_price = df_pe[df_pe['strikePrice'] == prev_atm_strike - (2 * get_strike_interval(symbolData))]['put_ltp'].values[0]

            # Save data to a dictionary along with the current time
            result_dict = {
                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'spot_price': spot_price,
                'pe_to_ce_ratio': pe_to_ce_ratio,
                'atm_strike': float(atm_ce_strike),
                'atm_current_ce_price': float(atm_ce_last_price),
                'atm_current_pe_price': float(atm_pe_last_price),
                'atm_next_ce_price': float(df_ce[df_ce['strikePrice'] == atm_ce_strike + (2 * get_strike_interval(symbolData))]['call_ltp'].values[0]),
                'atm_next_pe_price': float(df_pe[df_pe['strikePrice'] == atm_pe_strike - (2 * get_strike_interval(symbolData))]['put_ltp'].values[0]),
                'prev_atm_strike': prev_atm_strike,
                'prev_atm_ce_price': float(prev_atm_ce_price),
                'prev_atm_pe_price': float(prev_atm_pe_price),
                'prev_atm_next_ce_price': float(prev_atm_next_ce_price),
                'prev_atm_next_pe_price': float(prev_atm_pe_strike_price),
                'ce_strangle_strike': float(ce_strangle_strike),
                'pe_strangle_strike': float(pe_strangle_strike),
                'ce_strangle_price': float(df_ce[df_ce['strikePrice'] == ce_strangle_strike]['call_ltp'].values[0]),
                'pe_strangle_price': float(df_pe[df_pe['strikePrice'] == pe_strangle_strike]['put_ltp'].values[0]),
                'prev_strangle_ce_strike': prev_strangle_ce_strike,
                'prev_strangle_pe_strike': prev_strangle_pe_strike,
                'prev_ce_strangle_price': float(df_ce[df_ce['strikePrice'] == prev_strangle_ce_strike]['call_ltp'].values[0]),
                'prev_pe_strangle_price': float(df_pe[df_pe['strikePrice'] == prev_strangle_pe_strike]['put_ltp'].values[0]),
                'ce_highest_strike': float(ce_highest_values[0]),
                'ce_highest_open_interest': float(ce_highest_values[1]),
                'ce_highest_last_price': float(ce_highest_values[2]),
                'pe_highest_strike': float(pe_highest_values[0]),
                'pe_highest_open_interest': float(pe_highest_values[1]),
                'pe_highest_last_price': float(pe_highest_values[2]),
                'ce_second_highest_strike': float(ce_second_highest_values[0]),
                'ce_second_highest_open_interest': float(ce_second_highest_values[1]),
                'ce_second_highest_last_price': float(ce_second_highest_values[2]),
                'pe_second_highest_strike': float(pe_second_highest_values[0]),
                'pe_second_highest_open_interest': float(pe_second_highest_values[1]),
                'pe_second_highest_last_price': float(pe_second_highest_values[2]),
                'ce_third_highest_strike': float(ce_third_highest_values[0]),
                'ce_third_highest_open_interest': float(ce_third_highest_values[1]),
                'ce_third_highest_last_price': float(ce_third_highest_values[2]),
                'pe_third_highest_strike': float(pe_third_highest_values[0]),
                'pe_third_highest_open_interest': float(pe_third_highest_values[1]),
                'pe_third_highest_last_price': float(pe_third_highest_values[2])
            }
            return result_dict

        else:
            print("Failed to retrieve option chain data")
            return None

"""
symbol = "SENSEX"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.extract_options_data_bse(0, 0, 0, symbol)
print("BSE data \n")
print(option_chain_info)

option_chain_info =option_chain_analyzer.extract_options_data_groww(0, 0, 0, symbol)
print("Groww data \n")
print(option_chain_info)


# Example usage:
symbol = "NIFTY"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0, symbol)

# You can then access the information using option_chain_info
print(option_chain_info)

symbol = "BANKNIFTY"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(0, 0, 0, symbol)
print("Bank Nifty data \n")
# You can then access the information using option_chain_info
print(option_chain_info)


symbol = "FINNIFTY"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info = option_chain_analyzer.get_option_chain_info(0,0,0, symbol)

# You can then access the information using option_chain_info
print(option_chain_info)

symbol = "NIFTY"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info_groww = option_chain_analyzer.extract_options_data_groww(22400, 23300, 22300, symbol)
print("Groww data \n")
print(option_chain_info_groww)

option_chain_info = option_chain_analyzer.get_option_chain_info_nse(22400, 23300, 22300, symbol)
print("NSE data \n")
print(option_chain_info)

print("\n \n")

# find difference between two dictionaries
diff = {k: option_chain_info[k] for k in option_chain_info if option_chain_info[k] != option_chain_info_groww[k]}
print(diff)


symbol = "MIDCPNIFTY"
option_chain_analyzer = OptionChainData(symbol)
option_chain_info_groww = option_chain_analyzer.extract_options_data_groww(12650, 12200, 12800, symbol)
print("Groww data \n")
print(option_chain_info_groww)

option_chain_info = option_chain_analyzer.get_option_chain_info_nse(12650, 12200, 12800, symbol)
print("NSE data \n")
print(option_chain_info)

print("\n \n")

# find difference between two dictionaries
diff = {k: option_chain_info[k] for k in option_chain_info if option_chain_info[k] != option_chain_info_groww[k]}
print(diff)
"""
