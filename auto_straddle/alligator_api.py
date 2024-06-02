import pandas as pd

import warnings
warnings.simplefilter(action='ignore', category=FutureWarning)


class alligator_api(object):
    def __init__(self):
        self.smma_list = []        

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
            my_df = df.copy()
            my_df['median'] = (my_df['high'] + my_df['low']) / 2

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
            max_alligator = max(last_df.iloc[-1]['jaw'], last_df.iloc[-1]['teeth'], last_df.iloc[-1]['lips'])
            min_alligator = min(last_df.iloc[-1]['jaw'], last_df.iloc[-1]['teeth'], last_df.iloc[-1]['lips'])

            if min_alligator > last_df.iloc[-1]['close']:
                trend = "downtrend"

            if max_alligator < last_df.iloc[-1]['close']:
                trend = "uptrend"

            if (last_df.iloc[-2]['jaw'] > last_df.iloc[-2]['lips']) and (last_df.iloc[-1]['jaw'] < last_df.iloc[-1]['lips']):
                cross_over = "bullish"

            if (last_df.iloc[-2]['jaw'] < last_df.iloc[-2]['lips']) and (last_df.iloc[-1]['jaw'] > last_df.iloc[-1]['lips']):
                cross_over = "bearish"
        except Exception as e:
            print("compute_trend API failed: {}".format(e))
            trend = "excpetion"
            cross_over = "exception"

        return [trend, cross_over]

    @classmethod
    def WILLIAMS_FRACTAL(self, ohlc, period):
        """
        Williams Fractal Indicator
        Source: https://www.investopedia.com/terms/f/fractal.asp
        :param DataFrame ohlc: data
        :param int period: how many lower highs/higher lows the extremum value should be preceded and followed.
        :return DataFrame: fractals identified by boolean
        """

        def is_bullish_fractal(x):
            if x[period] == min(x):
                return True
            return False

        def is_bearish_fractal(x):
            if x[period] == max(x):
                return True
            return False

        window_size = period * 2 + 1
        bearish_fractals = pd.Series(
            ohlc.high.rolling(window=window_size, center=True).apply(
                is_bearish_fractal, raw=True
            ),
            name="BearishFractal",
        )
        bullish_fractals = pd.Series(
            ohlc.low.rolling(window=window_size, center=True).apply(
                is_bullish_fractal, raw=True
            ),
            name="BullishFractal",
        )
        return pd.concat([bearish_fractals, bullish_fractals], axis=1)
