"""
SEBI 50-50 Fund Optimizer
Monitors cash vs collateral split across accounts and generates weekly optimization reports.
SEBI requires F&O margins to be 50% cash and 50% non-cash (pledged equity collateral).
"""

import os
import logging
from datetime import datetime, time as time_dt
import pandas as pd

import configuration
from TelegramSend import telegram_send_api

logger = logging.getLogger(__name__)

ACCOUNTS = ['deepti', 'leelu', 'avanthi']
CSV_DIR = os.path.expanduser("~/temp/data_collection/csv")
CSV_PATH = os.path.join(CSV_DIR, "fund_snapshots.csv")

CSV_COLUMNS = [
    'timestamp', 'date', 'account', 'cash_balance', 'collateral',
    'margin_used', 'margin_available', 'holdings_value', 'net_balance',
    'cash_pct', 'collateral_pct',
]


SNAPSHOT_INTERVAL_MINUTES = 15


class FundOptimizer:
    def __init__(self):
        self.last_snapshot_time = None
        self.weekly_report_sent = False
        self.telegram = telegram_send_api()
        os.makedirs(CSV_DIR, exist_ok=True)

    def collect_snapshot(self, place_order):
        """Collect fund snapshots for all accounts every 15 minutes during market hours."""
        now = datetime.now()

        # Only during market hours on weekdays
        if now.weekday() >= 5:  # Saturday/Sunday
            return
        if not (time_dt(9, 15) <= now.time() <= time_dt(15, 30)):
            return

        # Throttle to every 15 minutes
        if self.last_snapshot_time is not None:
            elapsed = (now - self.last_snapshot_time).total_seconds()
            if elapsed < SNAPSHOT_INTERVAL_MINUTES * 60:
                return

        rows = []
        for account in ACCOUNTS:
            try:
                details = place_order.get_fund_details(account)
                if details is None:
                    logger.warning("Fund details returned None for %s", account)
                    continue

                cash = details['cash_balance']
                collateral = details['collateral']
                total = cash + collateral
                cash_pct = (cash / total * 100) if total > 0 else 0
                collateral_pct = (collateral / total * 100) if total > 0 else 0

                rows.append({
                    'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
                    'date': now.strftime("%Y-%m-%d"),
                    'account': account,
                    'cash_balance': round(cash, 2),
                    'collateral': round(collateral, 2),
                    'margin_used': round(details['margin_used'], 2),
                    'margin_available': round(details['margin_available'], 2),
                    'holdings_value': round(details['holdings_value'], 2),
                    'net_balance': round(total, 2),
                    'cash_pct': round(cash_pct, 1),
                    'collateral_pct': round(collateral_pct, 1),
                })
            except Exception as e:
                logger.error("Error collecting fund snapshot for %s: %s", account, e)

        if rows:
            df_new = pd.DataFrame(rows, columns=CSV_COLUMNS)
            if os.path.exists(CSV_PATH):
                df_new.to_csv(CSV_PATH, mode='a', header=False, index=False)
            else:
                df_new.to_csv(CSV_PATH, index=False)
            self.last_snapshot_time = now
            logger.info("Fund snapshots collected for %d accounts", len(rows))

    def generate_weekly_report(self):
        """Generate and send weekly report on Friday after 3:45 PM."""
        now = datetime.now()

        # Only on Friday after 3:45 PM
        if now.weekday() != 4:  # Not Friday
            return
        if now.time() < time_dt(15, 45):
            return
        if self.weekly_report_sent:
            return

        if not os.path.exists(CSV_PATH):
            logger.warning("No fund snapshots CSV found, skipping weekly report")
            return

        try:
            df = pd.read_csv(CSV_PATH)
            # Filter to this week's data
            today = now.strftime("%Y-%m-%d")
            # Get Monday of this week
            monday = (now - pd.Timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            df_week = df[df['date'] >= monday]

            if df_week.empty:
                logger.info("No fund data for this week, skipping report")
                return

            report = self._build_report(df_week, today)
            self._send_report(report)
            self.weekly_report_sent = True
            logger.info("Weekly fund report sent successfully")
        except Exception as e:
            logger.error("Error generating weekly report: %s", e)

    def reset_weekly_flag(self):
        """Reset weekly report flag on Monday."""
        if datetime.now().weekday() == 0:  # Monday
            self.weekly_report_sent = False

    def _build_report(self, df_week, today):
        """Build the weekly report string."""
        lines = [
            "SEBI 50-50 Fund Report",
            f"Week of {today}",
            "",
        ]

        for account in ACCOUNTS:
            df_acc = df_week[df_week['account'] == account]
            if df_acc.empty:
                continue

            # Use latest snapshot for the account
            latest = df_acc.iloc[-1]
            cash = latest['cash_balance']
            collateral = latest['collateral']
            cash_pct = latest['cash_pct']
            margin_used = latest['margin_used']
            holdings = latest['holdings_value']
            net = latest['net_balance']

            lines.append(account.upper())
            lines.append(f"Cash: {self._fmt(cash)} | Collateral: {self._fmt(collateral)}")
            lines.append(f"Cash%: {cash_pct:.0f}% | Margin Used: {self._fmt(margin_used)}")
            lines.append(f"Holdings: {self._fmt(holdings)}")

            # Recommendations
            if cash_pct > 70:
                ideal_cash = net * 0.5
                excess = cash - ideal_cash
                lines.append(f">> IDLE CASH: ~{self._fmt(excess)} can be pledged")
                unpledged = holdings - collateral
                if unpledged > 0:
                    lines.append(f"   Unpledged holdings: {self._fmt(unpledged)} (can pledge)")
            elif cash_pct < 40:
                ideal_cash = net * 0.5
                deficit = ideal_cash - cash
                lines.append(f">> LOW CASH: Need ~{self._fmt(deficit)} more cash")
            else:
                lines.append(">> OK: Cash/Collateral ratio within range")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _fmt(value):
        """Format number in Indian comma style (e.g., 55,45,585)."""
        if value < 0:
            return "-" + FundOptimizer._fmt(-value)
        value = int(round(value))
        s = str(value)
        if len(s) <= 3:
            return s
        # Last 3 digits, then groups of 2
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while rest:
            parts.append(rest[-2:])
            rest = rest[:-2]
        parts.reverse()
        return ",".join(parts) + "," + last3

    def _send_report(self, report):
        """Send the report to all account Telegram groups."""
        config = configuration.ConfigurationLoader.get_configuration()
        sent_to = set()
        for account in ACCOUNTS:
            telegram_key = account + "_telegram"
            group_id = config.get(telegram_key)
            if group_id and group_id not in sent_to:
                try:
                    self.telegram.send_message(group_id, report)
                    sent_to.add(group_id)
                    logger.info("Fund report sent to %s (%s)", account, group_id)
                except Exception as e:
                    logger.error("Failed to send fund report to %s: %s", account, e)
