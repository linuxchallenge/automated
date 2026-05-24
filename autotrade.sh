#!/bin/sh
# launcher.sh

while :
do
    current_time=$(date +%H:%M)  # Get the current time in HH:MM format

    if [ "$current_time" \> "08:30" ]; then
        # Execute the Python script if the time is after 8:30 AM
        /usr/bin/python3 /home/pitest/automated/auto_straddle/AutoStraddle.py
    fi

    sleep 1m  # Wait for 1 minute before checking the time again
done

