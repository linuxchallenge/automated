import requests
import pandas as pd
import time
from datetime import datetime
import os
import TelegramSend

import logging

logging.basicConfig(filename='option_chain.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

#save_path = '/home/pitest/data-collection/'

# Initialize the variables to track the previous pe_to_ce_ratio
previous_ratios = {}

def maximum(a, b, c): 
   list = [a, b, c] 
   return max(list) 

def minimum(a, b, c):
   list = [a, b, c]
   return min(list)

def get_option_chain_data_with_retry(url, max_retries=1, retry_delay=5):
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
            logging.error(f"Request failed on retry {retry + 1}. Error: {url}")
            if retry < max_retries:
                print(f"Retrying after {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                logging.error("Max retries exceeded. Unable to fetch data.")
                raise Exception("Max retries exceeded. Unable to fetch data.")


def extract_top_open_interest_values(df, top_n=3):
    df_with_open_interest = df[df['openInterest'] > 0]
    return df_with_open_interest.nlargest(top_n, 'openInterest')


def get_option_chain_info(url):
    try:
        option_chain_data = get_option_chain_data_with_retry(url)

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

        # Drop specified columns from df_ce and df_pe DataFrames
        columns_to_drop = ['expiryDate', 'underlying', 'underlyingValue', 'identifier', 'impliedVolatility', 'change',
                           'pChange',
                           'totalBuyQuantity', 'totalSellQuantity', 'bidQty', 'bidprice', 'askQty', 'askPrice',
                           'pchangeinOpenInterest']

        df_ce.drop(columns=columns_to_drop, inplace=True)
        df_pe.drop(columns=columns_to_drop, inplace=True)

        # Get the rows with the highest, second highest, and third highest openInterest in df_ce and df_pe
        ce_rows_sorted_by_open_interest = extract_top_open_interest_values(df_ce)
        pe_rows_sorted_by_open_interest = extract_top_open_interest_values(df_pe)

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

        pe_to_ce_ratio = total_open_interest_pe / total_open_interest_ce

        # Save data to a dictionary along with the current time
        result_dict = {
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'spot_price': spot_price,
            'pe_to_ce_ratio': pe_to_ce_ratio,
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


if __name__ == "__main__":
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    x = TelegramSend.telegram_send_api()

    pe_ratio_sentiments = {
    "NIFTY": -1,
    "BANKNIFTY": -1,
    "FINNIFTY": -1
}

    while True:
        current_time = datetime.now().time()
        start_time = datetime.strptime('09:32:00', '%H:%M:%S').time()
        end_time = datetime.strptime('23:00:00', '%H:%M:%S').time()

        logging.warning("Data analysis ")
        if start_time <= current_time <= end_time:
            # Initialize an empty DataFrame to store the option_chain_info
            data_frame = pd.DataFrame()

            for symbol in symbols:
                if symbol == "USDINR":
                    option_url = f"https://www.nseindia.com/api/option-chain-Currency?symbol={symbol}"
                else:
                    option_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"

                # Check if the CSV file exists for the current symbol
                csv_filename = f"{symbol}_{datetime.now().strftime('%Y-%m-%d')}.csv"
                #csv_filename = os.path.join(save_path, csv_filename)

                if os.path.exists(csv_filename):
                    # If the CSV file exists, read its content to initialize the data_frame
                    data_frame = pd.read_csv(csv_filename)
                else:
                    # If the CSV file does not exist, initialize an empty DataFrame
                    data_frame = pd.DataFrame()

                try:
                    option_chain_info = get_option_chain_info(option_url)
                    if option_chain_info:
                        # Append the option_chain_info to the DataFrame
                        data_frame = pd.concat([data_frame, pd.DataFrame([option_chain_info])], ignore_index=True)

                        # Save data_frame to CSV with the current date appended to the symbol
                        data_frame.to_csv(csv_filename, index=False)

                        if pe_ratio_sentiments[symbol] == -1:
                            pe_ratio_sentiments[symbol] = data_frame.iloc[0]['pe_to_ce_ratio']

                        #if pe_to_ce_ratio of data_frame changes by 0.2 in 10 minutes then print message
                        if data_frame.shape[0] > 2:
                            pe_to_ce_ratio = data_frame.iloc[-1]['pe_to_ce_ratio']
                            prev_pe_to_ce_ratio = pe_ratio_sentiments[symbol]

                            # Check if pe_to_ce_ratio crossed thresholds
                            if symbol in previous_ratios:
                                previous_ratio = previous_ratios[symbol]
                                if (pe_to_ce_ratio > 1.4 and previous_ratio <= 1.4) or (
                                        pe_to_ce_ratio < 0.7 and previous_ratio >= 0.7):
                                    str = (f"pe for '{symbol}' crossed : {pe_to_ce_ratio}")
                                    x.send_message("-958172193", str, '{symbol}')
                            previous_ratios[symbol] = pe_to_ce_ratio

                            if abs(pe_to_ce_ratio - prev_pe_to_ce_ratio) > 0.2:
                                str = (f"{symbol} changed from {pe_to_ce_ratio} to {prev_pe_to_ce_ratio}")
                                x.send_message("-958172193", str, '{symbol}')
                                pe_ratio_sentiments[symbol] = pe_to_ce_ratio

                        #if highest of ce_highest_strike, ce_second_highest_strike and ce_third_highest_strike
                        #  changes from previous minute then print message
                        if data_frame.shape[0] > 2:
                            ce_highest_strike = data_frame.iloc[-1]['ce_highest_strike']
                            ce_second_highest_strike = data_frame.iloc[-1]['ce_second_highest_strike']
                            ce_third_highest_strike = data_frame.iloc[-1]['ce_third_highest_strike']

                            prev_ce_highest_strike = data_frame.iloc[-2]['ce_highest_strike']
                            prev_ce_second_highest_strike = data_frame.iloc[-2]['ce_second_highest_strike']
                            prev_ce_third_highest_strike = data_frame.iloc[-2]['ce_third_highest_strike']

                            #pe highest strike pe second highest strike pe third highest strike
                            pe_highest_strike = data_frame.iloc[-1]['pe_highest_strike']
                            pe_second_highest_strike = data_frame.iloc[-1]['pe_second_highest_strike']
                            pe_third_highest_strike = data_frame.iloc[-1]['pe_third_highest_strike']

                            prev_pe_highest_strike = data_frame.iloc[-2]['pe_highest_strike']
                            prev_pe_second_highest_strike = data_frame.iloc[-2]['pe_second_highest_strike']
                            prev_pe_third_highest_strike = data_frame.iloc[-2]['pe_third_highest_strike']


                            # Max of ce_highest_strike, ce_second_highest_strike and ce_third_highest_strike is assigned to variable 'max'
                            # Max of prev_ce_highest_strike, prev_ce_second_highest_strike and prev_ce_third_highest_strike is assigned to variable 'prev_max'
                            # If max > prev_max then print message and send telegram message
                            max_ce_strike = maximum(ce_highest_strike, ce_second_highest_strike, ce_third_highest_strike)
                            max_previous_ce_strike = maximum(prev_ce_highest_strike, prev_ce_second_highest_strike, prev_ce_third_highest_strike)
                            if max_ce_strike != max_previous_ce_strike:
                                str = (f"Third ce {max_previous_ce_strike}, chamged to {max_ce_strike} of {symbol}")
                                x.send_message("-958172193", str, '{symbol}')

                            # Min of pe_highest_strike, pe_second_highest_strike and pe_third_highest_strike is assigned to variable 'min'
                            # Min of prev_pe_highest_strike, prev_pe_second_highest_strike and prev_pe_third_highest_strike is assigned to variable 'prev_min'
                            # If min < prev_min then print message and send telegram message
                            min_pe_strike = minimum(pe_highest_strike, pe_second_highest_strike, pe_third_highest_strike)
                            min_previous_pe_strike = minimum(prev_pe_highest_strike, prev_pe_second_highest_strike, prev_pe_third_highest_strike)
                            if min_pe_strike != min_previous_pe_strike:
                                str = (f"Third pe {min_previous_pe_strike}, chamged to {min_pe_strike} of {symbol}")
                                x.send_message("-958172193", str, '{symbol}')

                except Exception as e:
                    logging.error(f"Exception occurred for symbol '{symbol}': {e}")

        dt = datetime.now()
        time.sleep(60 - dt.second)