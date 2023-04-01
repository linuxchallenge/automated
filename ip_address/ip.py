import time
import socket
import fcntl
import struct
import TelegramSend


def get_interface_ipaddress(network):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', network[:15])
    )[20:24])


ip = get_interface_ipaddress('wlan0')
telegram_obj = TelegramSend.telegram_send_api()
telegram_obj.send_message("-950275666", str)
# Wait 5 seconds
