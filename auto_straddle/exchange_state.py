"""Module providing a function for far cell"""

# pylint: disable=W1203
# pylint: disable=W0718
# pylint: disable=C0301
# pylint: disable=C0116
# pylint: disable=C0115
# pylint: disable=C0103
# pylint: disable=W0105
# pylint: disable=C0200

import datetime
import requests

class ExchangeData:

    def is_mcx_open(self):
        exchange_data = self.get_exchange_data()
        if exchange_data:
            for item in exchange_data['data']:
                if item['exchange'] == 'MCX':
                    current_time = int(datetime.datetime.now().timestamp() *
                                       1000)
                    if item['start_time'] <= current_time <= item['end_time']:
                        return True
        return False

    def is_nfo_open(self):
        exchange_data = self.get_exchange_data()
        if exchange_data:
            for item in exchange_data['data']:
                if item['exchange'] == 'NFO':
                    current_time = int(datetime.datetime.now().timestamp() *
                                       1000)
                    if item['start_time'] <= current_time <= item['end_time']:
                        return True
        return False

    def get_exchange_data(self):
        today = datetime.date.today().strftime("%Y-%m-%d")
        url = f'https://api.upstox.com/v2/market/timings/{today}'
        headers = {'Accept': 'application/json'}

        response = requests.get(url, headers=headers, timeout=5)  # Add timeout argument

        if response.status_code == 200:
            data = response.json()
            # Process the JSON response
            print(data)
            return data
        else:
            print("Failed to retrieve data. Status code:",
                  response.status_code)
            return None

    # write a function in which for exchange MCX current it is open

'''
# Example usage
exchange_data = ExchangeData()
print(exchange_data.is_mcx_open())
print(exchange_data.is_nfo_open())
'''