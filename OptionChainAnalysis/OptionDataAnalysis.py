import requests
import pandas as pd
import time
from datetime import datetime
import os

import logging

logging.basicConfig(filename='/home/pitest/log/option_chain.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')


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
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "USDINR"]

    while True:
        current_time = datetime.now().time()
        start_time = datetime.strptime('09:18:00', '%H:%M:%S').time()
        end_time = datetime.strptime('17:00:00', '%H:%M:%S').time()

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

                except Exception as e:
                    print(f"Exception occurred for symbol '{symbol}': {e}")

        dt = datetime.now()
        time.sleep(60 - dt.second)
