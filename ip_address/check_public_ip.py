#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")

import requests
import os
import ipaddress
from datetime import datetime

# Use home directory of current user
IP_FILE = os.path.expanduser("~/.current_public_ip")
LOG_FILE = "/tmp/public_ip.log"

def log(message):
    """Append timestamped message to log file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} | {message}\n")

def is_valid_ip(text):
    """Check that text is a real IP address, not an error page"""
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False

def get_public_ip():
    """Get current public IPv4 address"""
    for url in ["https://api.ipify.org", "https://ipv4.icanhazip.com"]:
        try:
            response = requests.get(url, timeout=10)
            ip = response.text.strip()
            if response.status_code == 200 and is_valid_ip(ip):
                return ip
            log(f"ERROR: Bad response from {url}: {ip[:60]}")
        except:
            pass
    return None

def get_stored_ip():
    """Read previously stored IP"""
    if os.path.exists(IP_FILE):
        with open(IP_FILE, "r") as f:
            return f.read().strip()
    return None

def store_ip(ip):
    """Save current IP to file"""
    os.makedirs(os.path.dirname(IP_FILE), exist_ok=True)
    with open(IP_FILE, "w") as f:
        f.write(ip)

def main():
    current_ip = get_public_ip()
    
    if current_ip is None:
        log("ERROR: Could not fetch public IP")
        return
    
    stored_ip = get_stored_ip()
    
    if stored_ip is None:
        store_ip(current_ip)
        log(f"STARTED | IP: {current_ip}")
        import TelegramSend
        telegram_obj = TelegramSend.telegram_send_api()
        telegram_obj.send_message("-950275666", f"IP Monitor started. Current IP: {current_ip}")
    
    elif current_ip != stored_ip:
        store_ip(current_ip)
        log(f"CHANGED | Old: {stored_ip} | New: {current_ip}")
        import TelegramSend
        telegram_obj = TelegramSend.telegram_send_api()
        telegram_obj.send_message("-950275666", f"⚠️ PUBLIC IP CHANGED!\nOld: {stored_ip}\nNew: {current_ip}")
    
    else:
        log(f"OK | IP: {current_ip}")

if __name__ == "__main__":
    main()
