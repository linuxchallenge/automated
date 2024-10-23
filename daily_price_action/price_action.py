import pandas as pd
import numpy as np
import os
from data_fetch import DataFetcher
import datetime
import pandas as pd
import TelegramSend

def read_ohlc_data(csv_file):
    """Read OHLC data from a CSV file."""
    df = pd.read_csv(csv_file)
    required_columns = ['datetime', 'open', 'high', 'low', 'close']
    if not all(column in df.columns for column in required_columns):
        raise ValueError(f"CSV file must contain the following columns: {', '.join(required_columns)}")
    return df

def true_range(data):
    """Calculate the True Range (TR) for each row."""
    high_low = data['high'] - data['low']
    high_close_prev = abs(data['high'] - data['close'].shift(1))
    low_close_prev = abs(data['low'] - data['close'].shift(1))
    
    true_range_val = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    return true_range_val

def atr(data, length):
    """Calculate the Average True Range (ATR)."""
    tr_data = true_range(data)

    atr_series = tr_data.rolling(window=length, center=True, min_periods=1).mean()

    return atr_series

def base_candle(data):
    """Identify the base candle within the last 7 candles."""

    for count in range(1, 4):
        my_high_b = data['high'].iloc[count]
        my_low_b = data['low'].iloc[count]
        my_open_b = data['open'].iloc[count]
        my_close_b = data['close'].iloc[count]
        datetime = data['datetime'].iloc[count]

        print(f"high: {my_high_b}, low: {my_low_b}, open: {my_open_b}, close: {my_close_b}, datetime: {datetime}")

        candle_range = my_high_b - my_low_b
        body_range = abs(my_close_b - my_open_b)

        print(f"Body Range: {body_range}, Candle Range: {candle_range} , datetime: {datetime}")

        if body_range <= candle_range * 0.5:
            print(f"count: {count}")
            if (my_open_b - my_close_b) > 0:  # Bearish Candle
                print((my_close_b - my_low_b), 0.1 * candle_range, (my_high_b - my_close_b), 0.1 * candle_range)
                if (my_close_b - my_low_b) < 0.1 * candle_range or (my_high_b - my_close_b) < 0.1 * candle_range:
                    return count
            else:  # Bullish Candle
                print((my_high_b - my_close_b), 0.1 * candle_range, (my_open_b - my_low_b), 0.1 * candle_range)
                if (my_high_b - my_close_b) < 0.1 * candle_range or (my_open_b - my_low_b) < 0.1 * candle_range:
                    return count
        else:
            break
    return count

def calculate_ranges_and_strength(data):
    """Calculate Candle Range, Body Range, Volatility, and Strength."""
    candle_range = data['high'].iloc[0] - data['low'].iloc[0]
    body_range = abs(data['close'].iloc[0] - data['open'].iloc[0])

    #print(data['datetime'].iloc[0])

    strength = data['ATR'].iloc[0] * 1.2
    #print(" ============================ " +  data['datetime'].iloc[0]  + " ============================ ")    
    # Print strength value. body_range > candle_range * 0.5 and candle_range >= strength:
    print(f"datetime: {data['datetime'].iloc[0]}, strength: {strength}, candle_range: {candle_range}, body_range: {body_range}, atr: {data['ATR'].iloc[0]}")
    print(f"datetime: {data['datetime'].iloc[0]}, Open: {data['open'].iloc[0]}, close: {data['close'].iloc[0]}, high: {data['high'].iloc[0]}")
    if body_range > candle_range * 0.5 and candle_range >= strength:

        #print(" ============================ " +  data['datetime'].iloc[0]  + " ============================ ")

        print(f"datetime: {data['datetime'].iloc[0]}, Candle Range: {candle_range}, Body Range: {body_range}, strength: {strength}")
        number_base = base_candle(data)
        print(f"Number of base candles: {number_base}")
        if number_base is not None and 1 < number_base <= 3:
            print(data['datetime'].iloc[1])
            lowest = data['low'].iloc[1]
            for count_low in range(1, number_base):
                current_low = data['low'].iloc[count_low]
                if current_low < lowest:
                    lowest = current_low

            highest = data['high'].iloc[1]
            print(data['datetime'].iloc[1])
            for count_high in range(1, number_base):
                current_high = data['high'].iloc[count_high]
                if current_high > highest:
                    highest = current_high
            print(f"lowest: {lowest}, highest: {highest}")
            #print(" ============================ " +  data['datetime'].iloc[0]  + " ============================ ")

            return lowest, highest

    return None, None

def test_calculate_ranges_and_strength(ohlc_data):
    """Loop through the data and apply the function until a valid output is found."""

    dz_low, dz_high, sz_low, sz_high = None, None, None, None

    ohlc_data['ATR'] = atr(ohlc_data, 14)

    for i in range(0, len(ohlc_data)):  # Start from the 1th candle
        subset_data = ohlc_data.iloc[i:]

        # Get data from i to 0 to new data frame
        data = ohlc_data.iloc[:i,:]
        #print(data)
        #print(f"Line number: {i}")
        #print(subset_data)

        # print line number

        try:
            lowest, highest = calculate_ranges_and_strength(subset_data)
        except Exception as e:
            print(f"Error: {e}")
            lowest, highest = None, None
            
        if lowest is not None and highest is not None:

            if subset_data.iloc[0]['open']  > subset_data.iloc[0]['close']:

                # if data length is less than 1 then continue
                if len(data) < 1:
                    continue

                # Get highest of data high
                highest_high = data['high'].max()
                print(f"highest value of data['high']: {highest_high}")

                if highest_high > highest :
                    print("SZ vilaoted")
                    continue

                # get second highest of data high
                highest_high_second = data['high'].nlargest(2).iloc[-1]

                if highest_high_second > lowest:
                    print("SZ vilaoted")
                    continue

                if sz_high is not None and sz_low is not None:
                    continue
                print(f"!!!!!!!! Found sz_high: {highest}, sz_low: {lowest}")
                sz_high = highest
                sz_low = lowest
                sz_date = subset_data.iloc[0]['datetime']
            else:
                minimum_min = data['low'].min()
                print(f"lowest value of data['low']: {minimum_min}")

                if minimum_min < lowest :
                    print("DZ vilaoted")
                    continue

                # get second lowest of data low
                minimum_min_second = data['low'].nsmallest(2).iloc[-1]

                if minimum_min_second < highest:
                    print("DZ vilaoted")
                    continue

                if dz_high is not None and dz_low is not None:
                    continue
                print(f"!!!!!!!! Found dz_high: {highest}, dz_low: {lowest}")
                dz_high = highest
                dz_low = lowest
                dz_date = subset_data.iloc[0]['datetime']

        if dz_low is not None and dz_high is not None and sz_low is not None and sz_high is not None:
            print(f"dz_high: {dz_high}, dz_low: {dz_low}, dz_date: {dz_date}")
            print(f"sz_high: {sz_high}, sz_low: {sz_low}, sz_date: {sz_date}")
            print(f"Found on iteration: {i}")
            break
    else:
        if dz_low is not None and dz_high is not None:
            print(f"dz_high: {dz_high}, dz_low: {dz_low}, dz_date: {dz_date}")
        elif sz_low is not None and sz_high is not None:
            print(f"sz_high: {sz_high}, sz_low: {sz_low}, sz_date: {sz_date}")
        else:
            print("No valid range found within the data.")

    return dz_low, dz_high, sz_low, sz_high



# read ind_nifty500list.csv
os.chdir(os.path.dirname(os.path.abspath(__file__)))

csv_file = 'ind_nifty500list.csv'
nifty500 = pd.read_csv(csv_file)

# create DataFetcher object
data_fetcher = DataFetcher()

# Get today's date
today = datetime.date.today()

# Append today's date to the output file name
output_file = f"output_{today}.csv"

# create empty nifty500_output data frame
nifty500_output = pd.DataFrame()


# Loop throgh all row and get symbol
for index, row in nifty500.iterrows():
    symbol = row['Symbol']
    print(f"Processing symbol: {symbol}")

    # Fetch data
    #ohlc_data = data_fetcher.fetch_data(symbol, '1D')
    ohlc_data = data_fetcher.OHLCHistoricData(symbol)

    print(ohlc_data.head())
    try:
        dz_low, dz_high, sz_low, sz_high = test_calculate_ranges_and_strength(ohlc_data)
    except Exception as e:
        sz_low, sz_high, dz_low, dz_high = None, None, None, None
        pass

    try:    
        # Append values to data frame
        nifty500_output.loc[index, 'symbol'] = symbol
        nifty500_output.loc[index, 'sz_low'] = sz_low
        nifty500_output.loc[index, 'sz_high'] = sz_high
        nifty500_output.loc[index, 'dz_low'] = dz_low
        nifty500_output.loc[index, 'dz_high'] = dz_high
        nifty500_output.loc[index, 'close'] = ohlc_data.iloc[0]['close']
    except Exception as e:
        print(f"Error: {e}")
        continue

    # If ohlc_data[0] close - dz_high < 1 % then put near_dz yes
    if dz_high is not None:
        if abs(ohlc_data.iloc[0]['close'] - dz_low) < 0.03 * ohlc_data.iloc[0]['close']:
            nifty500_output.loc[index, 'near_dz'] = 'yes'
        else:
            nifty500_output.loc[index, 'near_dz'] = 'no'
    else:
        nifty500_output.loc[index, 'near_dz'] = 'no'

    # If ohlc_data[0] close - sz_high < 1 % then put near_sz yes
    if sz_low is not None:
        if abs(sz_high - ohlc_data.iloc[0]['close']) < 0.03 * ohlc_data.iloc[0]['close']:
            nifty500_output.loc[index, 'near_sz'] = 'yes'
        else:
            nifty500_output.loc[index, 'near_sz'] = 'no'
    else:
        nifty500_output.loc[index, 'near_sz'] = 'no'

# Save the output to a CSV file
nifty500_output.to_csv(output_file, index=False)
telegram_obj = TelegramSend.telegram_send_api()
telegram_obj.send_file("-891000076", output_file)
os.remove(output_file)

'''
# Example usage
csv_file = 'Data/stock/APLAPOLLO_1day.csv'
ohlc_data = read_ohlc_data(csv_file)

# Reverse the data to have the latest data at the end
ohlc_data = ohlc_data.iloc[::-1].reset_index(drop=True)

#ohlc_data['ATR'] = atr(ohlc_data, 14)
#print(ohlc_data[['datetime', 'high', 'low', 'close', 'ATR']].head(20))

test_calculate_ranges_and_strength(ohlc_data)
'''
