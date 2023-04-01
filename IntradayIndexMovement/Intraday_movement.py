import Intraday_api
import alligator_api
import macd_api
import TelegramSend
import usd
import pandas as pd
from datetime import datetime, timedelta
from enum import Enum
import time
import os
import logging
import sys


class Intraday_movement(object):

    def __init__(self):
        self.alligator = alligator_api.alligator_api()
        self.macd_obj = macd_api.macd_api()
        self.telegram_obj = TelegramSend.telegram_send_api()
        self.Intraday_api_obj = Intraday_api.intraday_api()
        self.usd_obj = usd.usd_api()

    def get_historic_data(self, fdate, todate, script_code):
        Dailydata = self.Intraday_api_obj.OHLCHistoricData(script_code, fdate, todate)
        my_df = pd.DataFrame(Dailydata)
        return my_df

    def get_historic_data_usd(self, time_frame):
        Dailydata = self.usd_obj.OHLCHistoricData(time_frame)
        my_df = pd.DataFrame(Dailydata)
        return my_df

    def compute_trend(self, my_df):
        test_df = self.alligator.compute_alligator(my_df)
        print(test_df)
        return self.alligator.compute_trend(test_df)


#logging.basicConfig(filename='/home/pitest/log/IntradayMovement.log', filemode='w',
logging.basicConfig(filename='/tmp/IntradayMovement.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

x = Intraday_movement()
logging.warning("Index movement")

nifty_trend = "Down"
bnf_trend = "Down"
fin_nifty = "Down"
usd_trend = "Down"

nifty_trend_75 = "Down"
bnf_trend_75 = "Down"
fin_nifty_75 = "Down"
usd_trend_60 = "Down"

dt = datetime.now()
# get day of week as an integer
weekday = dt.weekday()

#if weekday > 4:
#    logging.warning("Not a weekday")
#    sys.exit("Not a week day")

#if dt.hour < 9 | dt.hour > 15:
#    logging.warning("Not market hour")
#    sys.exit("Not market hour")

#if dt.hour == 9:
#    if dt.minute < 8:
#        logging.warning("Not market hour")
#        sys.exit("Not market hour")

#if dt.hour == 15:
#    if dt.minute > 30:
#        logging.warning("Not market hour")
#        sys.exit("Not market hour")

current = datetime.now()
seventy_days_before = current - timedelta(days=100)

df = x.get_historic_data(seventy_days_before, current, "Bnf")
bnf_trend = x.compute_trend(df)[0]

df = x.Intraday_api_obj.convert15m_to_75m(df)
bnf_trend_75 = x.compute_trend(df)[0]

df = x.get_historic_data(seventy_days_before, current, "Nifty")
nifty_trend = x.compute_trend(df)[0]

df = x.Intraday_api_obj.convert15m_to_75m(df)
nifty_trend_75 = x.compute_trend(df)[0]

df = x.get_historic_data(seventy_days_before, current, "Finnifty")
fin_nifty = x.compute_trend(df)[0]

df = x.Intraday_api_obj.convert15m_to_75m(df)
fin_nifty_75 = x.compute_trend(df)[0]


df = x.get_historic_data_usd(15)
usd_trend = x.compute_trend(df)[0]

df = x.get_historic_data_usd(60)
usd_trend_60 = x.compute_trend(df)[0]

bnf_trend_75_tmp = bnf_trend_75
nifty_trend_75_tmp = nifty_trend_75
fin_nifty_75_tmp = fin_nifty_75
usd_trend_60_tmp = usd_trend_60


str = "Bank nifty Trend: " + bnf_trend + "\n Nifty Trend: " + nifty_trend + "\n Fin Nifty Trend: " + fin_nifty + "\n USD INR Trend: " + usd_trend

str = str + "\nBank nifty 75 min Trend: " + bnf_trend_75 + "\n Nifty 75 m Trend: " + nifty_trend_75 + "\n Fin Nifty 75m " \
                                                                                                      " Trend: " + \
      fin_nifty_75 + "\n USD INR 60 min Trend: " + usd_trend_60

x.telegram_obj.send_message("-950275666", str)

while True:
    try:
        message_send = False
        dt = datetime.now()

        print(dt)

        str = "Trend update \n"

#        if dt.hour < 9 | dt.hour > 15:
#            logging.warning("Not market hour")
#            sys.exit("Not market hour")

#        if dt.hour == 9:
#            if dt.minute < 8:
#                logging.warning("Not market hour")
#                sys.exit("Not market hour")

#        if dt.hour == 15:
#            if dt.minute > 31:
#                logging.warning("Not market hour")
#                sys.exit("Not market hour")

        minute = (15 - dt.minute % 15)
        time.sleep(minute * 60)

        current = datetime.now()
        print(current)
        seventy_days_before = current - timedelta(days=70)

        df = x.get_historic_data(seventy_days_before, current, "Bnf")
        bnf_trend_tmp = x.compute_trend(df)[0]

        if ((dt.hour == 9 & dt.minute == 15) | (dt.hour == 10 & dt.minute == 30) | (dt.hour == 11 & dt.minute == 45)
                | (dt.hour == 13 & dt.minute == 0) | (dt.hour == 14 & dt.minute == 15) | (
                        dt.hour == 15 & dt.minute == 30)):
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            bnf_trend_75_tmp = x.compute_trend(df)[0]

        df = x.get_historic_data(seventy_days_before, current, "Nifty")
        nifty_trend_tmp = x.compute_trend(df)[0]

        if ((dt.hour == 9 & dt.minute == 15) | (dt.hour == 10 & dt.minute == 30) | (dt.hour == 11 & dt.minute == 45)
                | (dt.hour == 13 & dt.minute == 0) | (dt.hour == 14 & dt.minute == 15) | (
                        dt.hour == 15 & dt.minute == 30)):
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            nifty_trend_75_tmp = x.compute_trend(df)[0]

        df = x.get_historic_data(seventy_days_before, current, "Finnifty")
        fin_nifty_tmp = x.compute_trend(df)[0]

        if ((dt.hour == 9 & dt.minute == 15) | (dt.hour == 10 & dt.minute == 30) | (dt.hour == 11 & dt.minute == 45)
                | (dt.hour == 13 & dt.minute == 0) | (dt.hour == 14 & dt.minute == 15) | (
                        dt.hour == 15 & dt.minute == 30)):
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            fin_nifty_75_tmp = x.compute_trend(df)[0]

        df = x.get_historic_data_usd(15)
        usd_trend_tmp = x.compute_trend(df)[0]

        if dt.minute == 0:
            df = x.get_historic_data_usd(60)
            usd_trend_60_tmp = x.compute_trend(df)[0]

        if bnf_trend_tmp != bnf_trend:
            str = str + "BNF 15 min update " + bnf_trend + " to " + bnf_trend_tmp + "\n"
            bnf_trend = bnf_trend_tmp
            message_send = True

        if bnf_trend_75_tmp != bnf_trend_75:
            str = str + "BNF 75 min update " + bnf_trend_75 + " to " + bnf_trend_75_tmp + "\n"
            bnf_trend_75 = bnf_trend_75_tmp
            message_send = True

        if nifty_trend_tmp != nifty_trend:
            str = str + "Nifty 15 min update " + nifty_trend + " to " + nifty_trend_tmp + "\n"
            nifty_trend = nifty_trend_tmp
            message_send = True

        if nifty_trend_75_tmp != nifty_trend_75:
            str = str + "Nifty 75 min update " + nifty_trend_75 + " to " + nifty_trend_75_tmp + "\n"
            nifty_trend_75 = nifty_trend_75_tmp
            message_send = True

        if fin_nifty_tmp != fin_nifty:
            str = str + "Fin Nifty 15 min update " + fin_nifty + " to " + fin_nifty_tmp + "\n"
            fin_nifty = fin_nifty_tmp
            message_send = True

        if fin_nifty_75_tmp != fin_nifty_75:
            str = str + "Fin Nifty 75 min update " + fin_nifty_75 + " to " + fin_nifty_75_tmp + "\n"
            fin_nifty_75 = fin_nifty_75_tmp
            message_send = True

        if usd_trend != usd_trend_tmp:
            str = str + "USD Dollar 15 min update " + usd_trend + " to " + usd_trend_tmp + "\n"
            usd_trend = usd_trend_tmp
            message_send = True

        if usd_trend_60_tmp != usd_trend_60:
            str = str + "USD Dollar 60 min update " + usd_trend_60 + " to " + usd_trend_60_tmp + "\n"
            usd_trend_60 = usd_trend_60_tmp
            message_send = True

        if message_send:
            x.telegram_obj.send_message("-950275666", str)
    except Exception as e:
        logging.error("Failed: {}".format(e))
        print("Failed: {}".format(e))
        sys.exit("Exception !!")
