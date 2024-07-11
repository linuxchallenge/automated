import logging
import os
import pandas as pd
from datetime import datetime, timedelta
from enum import Enum
import time

import alligator_api
import macd_api
import TelegramSend
import yahooFin

class TimeFrame(Enum):
    HOURLY = 1
    DAILY = 2
    WEEKLY = 3

class DailySchedule:
    def __init__(self):
        self.alligator = alligator_api.alligator_api()
        self.macd_obj = macd_api.macd_api()
        self.telegram_obj = TelegramSend.telegram_send_api()
        self.nsepy_api_obj = yahooFin.yahooFin_api()

    def get_historic_data(self, fdate, todate, script_code):
        Dailydata = self.nsepy_api_obj.OHLCHistoricData(script_code, fdate, todate)
        return pd.DataFrame(Dailydata)

    def get_data(self, script_code, TimeFrame):
        current = datetime.now()
        if TimeFrame == TimeFrame.DAILY:
            year_days_before = current - timedelta(days=365)
            return self.get_historic_data(year_days_before, current,  script_code)
        if TimeFrame == TimeFrame.WEEKLY:
            ten_days_before = current - timedelta(days=1000)
            return self.process_weekly_data(self.get_historic_data(ten_days_before, current,  script_code))

    def process_weekly_data(self, my_df):
        my_df = my_df.reset_index()
        my_df['Date'] = pd.to_datetime(my_df.Date, format='%Y-%m-%d')
        my_df['Week_Number'] = my_df['Date'].dt.isocalendar().week
        my_df['Year'] = my_df['Date'].dt.isocalendar().year
        my_df = my_df.groupby(['Year', 'Week_Number']).agg(
            {'Date': 'first', 'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last',
             'Volume': 'sum'})
        my_df['median'] = (my_df['High'] + my_df['Low']) / 2
        return my_df

    def compute_trend(self, my_df):
        test_df = self.alligator.compute_alligator(my_df)
        return self.alligator.compute_trend(test_df)

    def run_daily_trend(self, nifty_200_df):
        final_df = pd.DataFrame(columns=['Script Name', 'ignore', 'Daily', 'Daily Cross over', 'Weekly', 'Weekly Cross over', 'Last traded', 'Buy Zone', 'Percentage daily','Percentage Weekly'])
        for index, row in nifty_200_df.iterrows():
            try:
                print(row["Symbol"])
                logging.warning(row["Symbol"])
                df = self.get_data(row["Symbol"], TimeFrame.DAILY)
                trend_analysis = [row["Company Name"], row["ignore"], *self.compute_trend(df)]
                daily = self.macd_obj.macd_api(df)
                percent_change_daily = self.calculate_percentage(df)
                df = self.get_data(row["Symbol"], TimeFrame.WEEKLY)
                trend_analysis.extend(self.compute_trend(df))
                trend_analysis.append(df["Close"].iloc[-1:].tolist()[0])
                percent = self.calculate_percentage(df, row["DZ"])
                percent_change_weekly = self.calculate_percentage(df)
                trend_analysis.extend([percent, percent_change_daily, percent_change_weekly])
                final_df.loc[len(final_df)] = trend_analysis
            except Exception as e:
                logging.error("Failed: {}".format(e))
                print("Failed: {}".format(e))

        return final_df

    def calculate_percentage(self, df, base=None):
        if base is None:
            base = df["Close"].iloc[-2:].tolist()[0]
        percent = (df["Close"].iloc[-1:].tolist()[0] - base) / df["Close"].iloc[-1:].tolist()[0]
        return percent * 100

    def teardown(self):
        pass

if __name__ == "__main__":
    logging.basicConfig(filename='app.log', filemode='w', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    path = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTMUidzjfyd-3sWfxwtAOGRneJQdCDpYBlb_EM9ylRxtFojS3OTV5XxLgfkI2CUP3K6qc8Kv_lZYiTo/pub?output=csv'
    nifty_200_df = pd.read_csv(path)
    x = DailySchedule()
    logging.warning("Running daily trend")
    final_df = x.run_daily_trend(nifty_200_df)
    filename = "DailyTrend_N200_" + str(datetime.now()) + ".csv"
    final_df.to_csv(filename)
    x.telegram_obj.send_file("-891000076", filename)
    os.remove(filename)
    x.teardown()