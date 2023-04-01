#https://stackoverflow.com/questions/24196932/how-can-i-get-the-ip-address-from-a-nic-network-interface-controller-in-python
#https://pypi.org/project/netifaces/

import netifaces as ni
import TelegramSend
import logging

logging.basicConfig(filename='app.log', filemode='w', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ip = ni.ifaddresses('wlan0')[ni.AF_INET][0]['addr']
print(ip)  # should print "192.168.100.37"
logging.warning("Ip address is")
logging.warning(ip)

x = TelegramSend.telegram_send_api()
str = "Pi IP is "
str = str + ip
x.send_message("-933716560", str)
