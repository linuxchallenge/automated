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

    def send_message(self, chat_id, message, type_index):
        payload = {
            'chat_id': chat_id,
            'disable_notification': False,
            'parse_mode': 'markdown',
        }
        payload['text'] = message
        files = {}
        method = 'sendMessage'
        dt = datetime.now()
        x = dt.weekday()

        if type_index == "BANKNIFTY" and x != 3:
            return

        if type_index == "FINNIFTY" and x != 2:
            return
        url = apiurl(token=token, method=method)
        print(url)
        response = requests.post(url, data=payload, files=files)

# x = telegram_send_api()
# x.send_file("-891000076", "file1.csv")
# x.send_message("-891000076", "file1.csv")
