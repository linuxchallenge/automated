import pandas as pd
from datetime import datetime, timedelta
from enum import Enum


class alligator_api(object):
    def __init__(self):
        pass


    def smma(self, src, length, future):
        smma = 0.0
        self.smma_list = []
        dataLength = len(src)
        lookbackPeriod = dataLength - length
        i = 0
        while i < (length + future - 1):
            self.smma_list.append(0)
            i = i + 1

        # first value of smma is the sma of src and length
        # Convert list to dataframe
        df = pd.DataFrame(src, columns=['hl2'])
        smma = df.rolling(window=length).mean()
        smma = float(smma.iloc[length])
        self.smma_list.append(smma)

        lookbackPeriod = length  # calculate smma for the other values
        while (lookbackPeriod < dataLength):
            smma = (smma * (length - 1) + float(src[lookbackPeriod])) / length
            lookbackPeriod = lookbackPeriod + 1
            self.smma_list.append(smma)

        self.smma_list = self.smma_list[:-1 * future]
        return self.smma_list

    def compute_alligator(self, df):
        try:
            my_df = df
            my_df['median'] = (my_df['High'] + my_df['Low']) / 2

            median_list = my_df['median'].tolist()
            smma_list = self.smma(median_list, 13, 8)
            my_df['jaw'] = smma_list
            smma_list = self.smma(median_list, 8, 5)
            my_df['teeth'] = smma_list
            smma_list = self.smma(median_list, 5, 3)
            my_df['lips'] = smma_list
        except Exception as e:
            print("compute_alligator API failed: {}".format(e))

        return my_df

    def compute_trend(self, last_df):
        trend = "sideways"
        cross_over = "no"
        try:
            max_alligator = max(last_df.iloc[-1][6], last_df.iloc[-1][7], last_df.iloc[-1][8])
            min_alligator = min(last_df.iloc[-1][6], last_df.iloc[-1][7], last_df.iloc[-1][8])

            if min_alligator > last_df.iloc[-1][1]:
                trend = "downtrend"

            if max_alligator < last_df.iloc[-1][2]:
                trend = "uptrend"

            if (last_df.iloc[-2][6] > last_df.iloc[-2][8]) and (last_df.iloc[-1][6] < last_df.iloc[-1][8]):
                cross_over = "bullish"

            if (last_df.iloc[-2][6] < last_df.iloc[-2][8]) and (last_df.iloc[-1][6] > last_df.iloc[-1][8]):
                cross_over = "bearish"
        except Exception as e:
            print("compute_trend API failed: {}".format(e))
            trend = "excpetion"
            cross_over = "exception"

        return [trend, cross_over]
