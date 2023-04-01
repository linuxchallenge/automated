import pandas_ta as ta


class macd_api(object):
    def __init__(self):
        pass

    def macd_api(self, src):
        macd_df = ta.macd(src['Close'])
        #print(macd_df)
        cross_over = "Neutral"
        try:
            if (macd_df.iloc[-2]['MACD_12_26_9'] > macd_df.iloc[-2]['MACDs_12_26_9']) and (macd_df.iloc[-1]['MACD_12_26_9'] < macd_df.iloc[-1]['MACDs_12_26_9']):
                if (macd_df.iloc[-2]['MACD_12_26_9'] > 0 and macd_df.iloc[-2]['MACDs_12_26_9'] > 0):
                    cross_over = "bearish"

            if (macd_df.iloc[-2]['MACD_12_26_9'] < macd_df.iloc[-2]['MACDs_12_26_9']) and (macd_df.iloc[-1]['MACD_12_26_9'] > macd_df.iloc[-1]['MACDs_12_26_9']):
                if (macd_df.iloc[-2]['MACD_12_26_9'] < 0 and macd_df.iloc[-2]['MACDs_12_26_9'] < 0):
                    cross_over = "bullish"
        except Exception as e:
            print("MACD API failed: {}".format(e))

        #print(cross_over)
        return cross_over
