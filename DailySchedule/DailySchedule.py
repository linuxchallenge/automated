#import nsepy_api
import yahooFin
import alligator_api
import macd_api
import TelegramSend
import pandas as pd
from datetime import datetime, timedelta
from enum import Enum
import time
import os
import logging

class DailySchedule(object):
    class TimeFrame(Enum):
        HOURLY = 1
        DAILY = 2
        WEEKLY = 3

    def __init__(self):
        self.alligator = alligator_api.alligator_api()
        self.macd_obj = macd_api.macd_api()
        self.telegram_obj = TelegramSend.telegram_send_api()
        #self.nsepy_api_obj = nsepy_api.nse_pi_api()
        self.nsepy_api_obj = yahooFin.yahooFin_api()

    def get_historic_data(self, fdate, todate, script_code):
        Dailydata = self.nsepy_api_obj.OHLCHistoricData(script_code, fdate, todate)
        my_df = pd.DataFrame(Dailydata)
        return my_df

    def teardown(self):
        pass

    def get_data(self, script_code, TimeFrame):
        current = datetime.now()
        if TimeFrame == x.TimeFrame.DAILY:
            year_days_before = current - timedelta(days=365)
            my_df = self.get_historic_data(year_days_before, current,  script_code)
            #my_df = my_df.set_index('Date')
            return my_df
        if TimeFrame == x.TimeFrame.WEEKLY:
            ten_days_before = current - timedelta(days=1000)
            my_df = self.get_historic_data(ten_days_before, current,  script_code)
            #print(my_df)
            my_df = my_df.reset_index()
            my_df['Date'] = pd.to_datetime(my_df.Date, format='%Y-%m-%d')
            my_df['Week_Number'] = my_df['Date'].dt.isocalendar().week

            # Getting year. Weeknum is common across years to we need to create unique index by using year and weeknum
            my_df['Year'] = my_df['Date'].dt.isocalendar().year
            my_df = my_df.groupby(['Year', 'Week_Number']).agg(
                {'Date': 'first', 'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last',
                 'Volume': 'sum'})
            my_df['median'] = (my_df['High'] + my_df['Low']) / 2
            my_df = my_df.set_index('Date')
            return my_df

    def compute_trend(self, my_df):
        test_df = self.alligator.compute_alligator(my_df)
        return self.alligator.compute_trend(test_df)

logging.basicConfig(filename='app.log', filemode='w', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQrCx2i8srLik2T4Rtpuq5c0qcYJknaDLcb9d2KTsJrIZmyucbzUE1LBob-7TVDlsyVUt4eSgVrkZv-/pub?output=csv'
#nifty_200_df = pd.read_csv('ind_nifty200list.csv')
nifty_200_df = pd.read_csv(path)

final_df = pd.DataFrame(columns=['Script Name', 'ignore', 'Daily', 'Daily Cross over', 'Weekly', 'Weekly Cross over', 'MACD Daily', 'MACD Weekly', 'Last traded', 'Buy Zone'])
x = DailySchedule()

logging.warning("Running daly trend")

# Iterate all rows using DataFrame.iterrows()
for index, row in nifty_200_df.iterrows():
    try:
        #time.sleep(1)
        print(row["Symbol"])
        logging.warning(row["Symbol"])
        df = x.get_data(row["Symbol"], x.TimeFrame.DAILY)
        trend_analysis = [row["Company Name"], row["ignore"], x.compute_trend(df)[0], x.compute_trend(df)[1]]
        daily = x.macd_obj.macd_api(df)
        #time.sleep(1)
        df = x.get_data(row["Symbol"], x.TimeFrame.WEEKLY)
        trend_analysis.append(x.compute_trend(df)[0])
        trend_analysis.append(x.compute_trend(df)[1])
        print (x.compute_trend(df)[0], x.compute_trend(df)[1])
        trend_analysis.append(daily)
        trend_analysis.append(x.macd_obj.macd_api(df))
        trend_analysis.append(df["Close"].iloc[-1:].tolist()[0])
        percent = (df["Close"].iloc[-1:].tolist()[0] - row["DZ"]) / df["Close"].iloc[-1:].tolist()[0]
        percent = percent * 100
        if percent < 3:
            trend_analysis.append("Yes")
        else:
            trend_analysis.append("No")
        final_df.loc[len(final_df)] = trend_analysis
    except Exception as e:
        logging.error("Failed: {}".format(e))
        print("Failed: {}".format(e))

filename = "DailyTrend_N200_" + str(datetime.now()) + ".csv"
final_df.to_csv(filename)
x.telegram_obj.send_file("-891000076", filename)
os.remove(filename)
x.teardown()
