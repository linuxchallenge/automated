import pandas_ta as ta
import requests
import logging

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
        logging.warning(url)
        print(url)
        i = 1
        while i < 6:
            response = requests.post(url, data=payload, files=files)
            logging.warning(response)
            i += 1
            if response.status_code == 200:
                break

#x = telegram_send_api()
#x.send_file("-891000076", "file1.csv")
#x.send_message("-891000076", "file1.csv")

