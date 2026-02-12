#pylint: disable=C0103
#pylint: disable=C0114
#pylint: disable=C0301

import smtplib
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def get_todays_log_file():
    """Get the path to today's log file."""
    date_str = datetime.now().strftime('%Y-%m-%d')
    return f"/tmp/auto_trade_{date_str}.log"


def extract_errors_from_log(filepath):
    """Parse the log file and extract all ERROR level log lines.

    Returns a list of error lines. Multi-line errors (like tracebacks)
    are grouped with the preceding ERROR line.
    """
    error_list = []
    if not os.path.exists(filepath):
        return [f"Log file not found: {filepath}"]

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            current_error = None
            for line in f:
                # Check if this is a new log line (starts with a timestamp)
                is_new_log_line = len(line) > 25 and line[4] == '-' and line[7] == '-' and line[10] == ' '

                if '[ERROR]' in line:
                    # Save previous error if exists
                    if current_error is not None:
                        error_list.append(current_error)
                    current_error = line.rstrip()
                elif current_error is not None:
                    if is_new_log_line:
                        # New log line (not a continuation) - save current error and reset
                        error_list.append(current_error)
                        current_error = None
                    else:
                        # Continuation line (e.g., traceback) - append to current error
                        current_error += '\n' + line.rstrip()

            # Don't forget the last error
            if current_error is not None:
                error_list.append(current_error)
    except (OSError, IOError) as e:
        error_list.append(f"Error reading log file: {e}")

    return error_list


def send_email_with_errors(sender_email, sender_password, receiver_email, email_subject, email_body):
    """Send an email with the error summary as the body."""
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = email_subject

    msg.attach(MIMEText(email_body, 'plain'))

    try:
        print(f"Connecting to smtp.gmail.com:587...")
        s = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
        s.set_debuglevel(0)
        print("Starting TLS...")
        s.starttls()
        print(f"Logging in as {sender_email}...")
        s.login(sender_email, sender_password)
        text = msg.as_string()
        print(f"Sending email to {receiver_email} (size: {len(text)} bytes)...")
        refused = s.sendmail(sender_email, receiver_email, text)
        s.quit()
        if refused:
            print(f"Email refused for recipients: {refused}")
        else:
            print("Email sent successfully!")
    except (smtplib.SMTPException, OSError) as e:
        print(f"Failed to send email. Error: {type(e).__name__}: {e}")


# --- CONFIGURATION ---
SENDER = "wirelessjobsearch@gmail.com"
PASSWORD = "zniy srnt jlqd tpeg"
RECEIVER = "linux.challenge1@gmail.com"

if __name__ == "__main__":
    log_file = get_todays_log_file()
    today = datetime.now().strftime('%Y-%m-%d')

    errors = extract_errors_from_log(log_file)

    if errors:
        subject = f"AutoStraddle Errors - {today} ({len(errors)} errors)"
        body = f"Error summary from: {log_file}\n"
        body += f"Date: {today}\n"
        body += f"Total errors: {len(errors)}\n"
        body += "=" * 80 + "\n\n"

        for i, error in enumerate(errors, 1):
            body += f"--- Error {i} ---\n"
            body += error + "\n\n"

        body += "=" * 80 + "\n"
        body += "End of error report\n"
    else:
        subject = f"AutoStraddle - No Errors - {today}"
        body = f"No errors found in log file: {log_file}\nDate: {today}\n"

    send_email_with_errors(SENDER, PASSWORD, RECEIVER, subject, body)
