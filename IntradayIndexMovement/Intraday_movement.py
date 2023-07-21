import Intraday_api
import alligator_api
import macd_api
import TelegramSend
import usd
import pandas as pd
from datetime import datetime, timedelta
import time
import logging
import sys
import csv
import os.path


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
        if time_frame == 60:
            my_df = self.usd_obj.convert30m_to_60m(my_df)
        return my_df

    def compute_trend(self, my_df):
        test_df = self.alligator.compute_alligator(my_df)
        # print(test_df)
        return self.alligator.compute_trend(test_df)

    def write_to_csv(self, dictionary, filename):
        # Write the dictionary to a CSV file
        with open(filename, 'w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=dictionary.keys())
            writer.writeheader()
            writer.writerow(dictionary)

    def read_from_csv(self, filename):
        # Read the dictionary from the CSV file
        with open(filename, 'r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                return row


x = Intraday_movement()

logging.basicConfig(filename='/home/pitest/log/IntradayMovement.log', filemode='w',
                    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s')

file_exists = os.path.isfile("data.csv")
if file_exists:
    trend_default = x.read_from_csv("data.csv")

else:
    trend_default = {"nifty_trend": "Down", "bnf_trend": "Down", "fin_nifty": "Down", "usd_trend": "Down",
                     "nifty_trend_75": "Down", "bnf_trend_75": "Down", "fin_nifty_75": "Down", "usd_trend_75": "Down"}

logging.warning("Index movement")

dt = datetime.now()
# get day of week as an integer
weekday = dt.weekday()

current = datetime.now()
seventy_days_before = current - timedelta(days=100)

str = "Bank nifty Trend: " + trend_default["bnf_trend"] + "\n Nifty Trend: " + trend_default["nifty_trend"] + \
      "\n Fin Nifty Trend: " + trend_default["fin_nifty"] + "\n USD INR Trend: " + trend_default["usd_trend"]
str = str + "\nBank nifty 75 min Trend: " + trend_default["bnf_trend_75"] + "\n Nifty 75 m Trend: " + trend_default["nifty_trend_75"] + "\n Fin Nifty " \
                                                                                                              " Trend: " + \
      trend_default["fin_nifty_75"] + "\n USD INR 60 min Trend: " + trend_default["usd_trend_60"]

logging.warning("Default value is \n")
logging.warning(str)

bnf_trend_75_tmp = trend_default["bnf_trend_75"]
nifty_trend_75_tmp = trend_default["nifty_trend_75"]
fin_nifty_75_tmp = trend_default["fin_nifty_75"]
usd_trend_60_tmp = trend_default["usd_trend_60"]

try:
    df = x.get_historic_data(seventy_days_before, current, "Bnf")
    trend_default["bnf_trend"] = x.compute_trend(df)[0]

    df = x.Intraday_api_obj.convert15m_to_75m(df)
    trend_default["bnf_trend_75"] = x.compute_trend(df)[0]

    df = x.get_historic_data(seventy_days_before, current, "Nifty")
    trend_default["nifty_trend"] = x.compute_trend(df)[0]

    df = x.Intraday_api_obj.convert15m_to_75m(df)
    trend_default["nifty_trend_75"] = x.compute_trend(df)[0]

    df = x.get_historic_data(seventy_days_before, current, "Finnifty")
    trend_default["fin_nifty"] = x.compute_trend(df)[0]

    df = x.Intraday_api_obj.convert15m_to_75m(df)
    trend_default["fin_nifty_75"] = x.compute_trend(df)[0]

    df = x.get_historic_data_usd(15)
    trend_default["usd_trend"] = x.compute_trend(df)[0]

    df = x.get_historic_data_usd(60)
    trend_default["usd_trend_60"] = x.compute_trend(df)[0]

    str = "Bank nifty Trend: " + trend_default["bnf_trend"] + "\n Nifty Trend: " + trend_default["nifty_trend"] + \
          "\n Fin Nifty Trend: " + trend_default["fin_nifty"] + "\n USD INR Trend: " + trend_default["usd_trend"]

    str = str + "\nBank nifty 75 min Trend: " + bnf_trend_75_tmp + "\n Nifty 75 m Trend: " + nifty_trend_75_tmp + "\n Fin Nifty " \
                                                                                                                  "75m " \
                                                                                                                  " Trend: " + \
          fin_nifty_75_tmp + "\n USD INR 60 min Trend: " + usd_trend_60_tmp

    # x.telegram_obj.send_message("-950275666", str)
    print(str)
    logging.warning(str)
except Exception as e:
    logging.error("Failed: {}".format(e))
    print("Failed: {}".format(e))

while True:
    try:
        message_send = False
        dt = datetime.now()
        logging.warning("New loop")
        logging.warning(dt)

        x.write_to_csv(trend_default, "data.csv")

        str = "Trend update \n"

        minute = (15 - dt.minute % 15)
        time.sleep(minute * 60)

        current = datetime.now()
        logging.warning("After sleep")
        logging.warning(current)
        seventy_days_before = current - timedelta(days=70)

        df = x.get_historic_data(seventy_days_before, current, "Bnf")
        bnf_trend_tmp = x.compute_trend(df)[0]
        logging.warning("Trend BNF 15 min " + bnf_trend_tmp + " " + trend_default["bnf_trend"])

        if (current.hour == 9 and current.minute == 15) or (current.hour == 10 and current.minute == 30) or (
                current.hour == 11 and current.minute == 45) or (current.hour == 13 and current.minute == 0) or (
                current.hour == 14 and current.minute == 15) or (current.hour == 15 and current.minute == 30) or (
                current.hour == 16 and current.minute == 15):
            logging.warning("Checking bnf 75 min")
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            bnf_trend_75_tmp = x.compute_trend(df)[0]
            logging.warning("Trend BNF 75 min " + bnf_trend_75_tmp + " " + trend_default["bnf_trend_75"])

        df = x.get_historic_data(seventy_days_before, current, "Nifty")
        nifty_trend_tmp = x.compute_trend(df)[0]

        logging.warning("Trend NF 15 min " + nifty_trend_tmp + " " + trend_default["bnf_trend"])

        if (current.hour == 9 and current.minute == 15) or (current.hour == 10 and current.minute == 30) or (
                current.hour == 11 and current.minute == 45) or (current.hour == 13 and current.minute == 0) or (
                current.hour == 14 and current.minute == 15) or (current.hour == 15 and current.minute == 30):
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            nifty_trend_75_tmp = x.compute_trend(df)[0]
            logging.warning("Trend NF 75 min " + nifty_trend_75_tmp + " " + trend_default["nifty_trend_75"])

        df = x.get_historic_data(seventy_days_before, current, "Finnifty")
        fin_nifty_tmp = x.compute_trend(df)[0]
        logging.warning("Trend Fin NF 15 min " + fin_nifty_tmp + " " + trend_default["fin_nifty"])

        if (current.hour == 9 and current.minute == 15) or (current.hour == 10 and current.minute == 30) or (
                current.hour == 11 and current.minute == 45) or (current.hour == 13 and current.minute == 0) or (
                current.hour == 14 and current.minute == 15) or (current.hour == 15 and current.minute == 30):
            df = x.Intraday_api_obj.convert15m_to_75m(df)
            fin_nifty_75_tmp = x.compute_trend(df)[0]
            logging.warning("Trend Fin NF 75 min " + fin_nifty_75_tmp + " " + trend_default["fin_nifty_75"])

        df = x.get_historic_data_usd(15)
        usd_trend_tmp = x.compute_trend(df)[0]
        logging.warning("Trend USD INR 15 min " + usd_trend_tmp + " " + trend_default["usd_trend"])

        if current.minute == 0:
            df = x.get_historic_data_usd(60)
            usd_trend_60_tmp = x.compute_trend(df)[0]
            logging.warning("Trend USD INR 60 min " + usd_trend_60_tmp + " " + trend_default["usd_trend_60"])

        if bnf_trend_tmp != trend_default["bnf_trend"]:
            str = str + "BNF 15 min update " + trend_default["bnf_trend"] + " to " + bnf_trend_tmp + "\n"
            trend_default["bnf_trend"] = bnf_trend_tmp
            message_send = True

        if bnf_trend_75_tmp != trend_default["bnf_trend_75"]:
            str = str + "BNF 75 min update " + trend_default["bnf_trend_75"] + " to " + bnf_trend_75_tmp + "\n"
            trend_default["bnf_trend_75"] = bnf_trend_75_tmp
            message_send = True

        if nifty_trend_tmp != trend_default["nifty_trend"]:
            str = str + "Nifty 15 min update " + trend_default["nifty_trend"] + " to " + nifty_trend_tmp + "\n"
            trend_default["nifty_trend"] = nifty_trend_tmp
            message_send = True

        if nifty_trend_75_tmp != trend_default["nifty_trend_75"]:
            str = str + "Nifty 75 min update " + trend_default["nifty_trend_75"] + " to " + nifty_trend_75_tmp + "\n"
            trend_default["nifty_trend_75"] = nifty_trend_75_tmp
            message_send = True

        if fin_nifty_tmp != trend_default["fin_nifty"]:
            str = str + "Fin Nifty 15 min update " + trend_default["fin_nifty"] + " to " + fin_nifty_tmp + "\n"
            trend_default["fin_nifty"] = fin_nifty_tmp
            message_send = True

        if fin_nifty_75_tmp != trend_default["fin_nifty_75"]:
            str = str + "Fin Nifty 75 min update " + trend_default["fin_nifty_75"] + " to " + fin_nifty_75_tmp + "\n"
            trend_default["fin_nifty_75"] = fin_nifty_75_tmp
            message_send = True

        if usd_trend_tmp != trend_default["usd_trend"]:
            str = str + "USD Dollar 15 min update " + trend_default["usd_trend"] + " to " + usd_trend_tmp + "\n"
            trend_default["usd_trend"] = usd_trend_tmp

        if usd_trend_60_tmp != trend_default["usd_trend_60"]:
            str = str + "USD Dollar 60 min update " + trend_default["usd_trend_60"] + " to " + usd_trend_60_tmp + "\n"
            trend_default["usd_trend_60"] = usd_trend_60_tmp
            message_send = True

        logging.warning(str)
        if message_send:
            x.telegram_obj.send_message("-950275666", str)

        x.write_to_csv(trend_default, "data.csv")

    except Exception as e:
        logging.error("Failed: {}".format(e))
        print("Failed: {}".format(e))
