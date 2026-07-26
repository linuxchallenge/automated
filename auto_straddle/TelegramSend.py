"""Module providing a function for main function """

# pylint: disable=W1203
# pylint: disable=W1201
# pylint: disable=W1202
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105

import logging
import requests

logger = logging.getLogger(__name__)

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
        try:
            response = requests.post(url, data=payload, files=files, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Telegram send_file failed ({response.status_code}): {response.text}")
                response = requests.post(url, data=payload, files=files, timeout=10)
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            try:
                response = requests.post(url, data=payload, files=files, timeout=10)
            except Exception as e2:
                logger.error(f"Error sending file on retry: {e2}")
        finally:
            files['document'].close()

    def send_photo(self, chat_id, file):
        """Send an image so it renders inline in the chat (unlike send_file)."""
        payload = {
            'chat_id': chat_id,
            'disable_notification': False,
            'parse_mode': 'markdown',
        }
        files = {}
        method = 'sendPhoto'
        files['photo'] = open(file, "rb")

        url = apiurl(token=token, method=method)
        try:
            response = requests.post(url, data=payload, files=files, timeout=30)
            if response.status_code != 200:
                logger.warning(f"Telegram send_photo failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Error sending photo: {e}")
        finally:
            files['photo'].close()

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
        try:
            response = requests.post(url, data=payload, files=files, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Telegram send_message failed ({response.status_code}): {response.text}")
                # Retry without markdown parse_mode in case of formatting issues
                payload.pop('parse_mode', None)
                response = requests.post(url, data=payload, files=files, timeout=10)
                if response.status_code != 200:
                    raise Exception(f"Telegram API error {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            try:
                payload.pop('parse_mode', None)
                response = requests.post(url, data=payload, files=files, timeout=10)
            except Exception as e2:
                logger.error(f"Error sending message on retry: {e2}")
                raise

#x = telegram_send_api()
#x.send_file("-4008545231", "sold_options_info_2024-01-06_account1_UnderlyingSymbol.BANKNIFTY.csv")
#x.send_message("-4008545231", "file1.csv")
