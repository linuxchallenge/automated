import pandas_ta as ta
import requests
from datetime import datetime

apiurl = 'https://api.telegram.org/bot{token}/{method}'.format
token = '5924214275:AAGdOZwDp72f15flvxok3NX_v3eqr0LjuT8'


class telegram_send_api(object):
    def __init__(self):
        pass

    def send_file(self, chat_id, file):
        payload = {
            'chat_id': chat_id,
            'disable_notification': False,
            'parse_mode': 'markdown',
        }
        files = {}
        method = 'sendDocument'
        files['document'] = open(file, "rb")

        url = apiurl(token=token, method=method)
        response = requests.post(url, data=payload, files=files)
        files['document'].close()

    def send_message(self, chat_id, message):
        payload = {
            'chat_id': chat_id,
            'disable_notification': False,
            'parse_mode': 'markdown',
        }
        payload['text'] = message
        files = {}
        method = 'sendMessage'
        url = apiurl(token=token, method=method)
        print(url)
        response = requests.post(url, data=payload, files=files)

#x = telegram_send_api()
#x.send_file("-4008545231", "sold_options_info_2024-01-06_account1_UnderlyingSymbol.BANKNIFTY.csv")
#x.send_message("-4008545231", "file1.csv")
