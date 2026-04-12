"""
Module for fetching CSV data from Google Sheets and managing trade files.
"""

import io
import os
import json
import time
import shutil
import logging
import requests
import pandas as pd
from datetime import datetime
import logging_config  # pylint: disable=unused-import  # side-effect: configures logging

# Initialize logging using the common config
logger = logging.getLogger(__name__)

class CSVFetcher:
    def __init__(self):
        self.csv_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vT2NS5gpElTvclT3GO93pYzaHOAg5dWlTzW9E-ZUUO8WhXuXpI2XLvUijVIVh6bIPQ8zlOWhp5lFbMo/pub?output=csv"
        self.source_dir = os.path.expanduser("~/temp/data_collection/csv")
        self.dest_dir = os.getcwd()
        self.tracking_file = os.path.join(self.source_dir, "processed_files.json")
        self.processed_data = self._load_tracking()

    def _load_tracking(self):
        """Load the local record of processed files."""
        if os.path.exists(self.tracking_file):
            try:
                with open(self.tracking_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading tracking file: {e}")
                return {}
        return {}

    def _save_tracking(self):
        """Save the local record of processed files."""
        try:
            with open(self.tracking_file, 'w') as f:
                json.dump(self.processed_data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving tracking file: {e}")

    def fetch_and_process(self, once=False):
        """Fetch CSV, identify files, and move them."""
        logger.info("Starting fetch and process cycle...")
        try:
            # Fetch CSV data
            response = requests.get(self.csv_url, timeout=30)
            response.raise_for_status()

            # Load into DataFrame
            # The columns are expected to be: Sl no, Instrument, account, stratergy, expiry, date
            df = pd.read_csv(io.StringIO(response.text))

            if df.empty:
                logger.info("CSV is empty. Nothing to process.")
                return

            for _, row in df.iterrows():
                self._process_row(row)

            self._save_tracking()
            logger.info("Fetch and process cycle completed.")

        except Exception as e:
            logger.error(f"Error in fetch_and_process: {e}")

        if once:
            logger.info("Single run completed. Exiting.")
            exit(0)

    def _process_row(self, row):
        """Process a single row from the CSV."""
        try:
            # Extract info
            sl_no = str(row.get('Sl no', ''))
            instrument = str(row.get('Instrument', ''))
            account = str(row.get('account', ''))
            strategy = str(row.get('stratergy', ''))
            expiry = str(row.get('expiry', ''))
            date_col = str(row.get('date', '')) # e.g., 2026-01-07:19-21

            if not all([instrument, account, strategy, expiry]):
                logger.warning(f"Skipping incomplete row: {row.to_dict()}")
                return

            # Construct the target filename
            # nifty_pos_options_info_2026-01-08_avanthi_SENSEX_fr.csv
            filename = f"nifty_pos_options_info_{expiry}_{account}_{instrument}_{strategy}.csv"

            # Unique key for tracking (filename + date_col to be safe)
            tracking_key = f"{filename}_{date_col}"

            if tracking_key in self.processed_data:
                logger.debug(f"File already processed: {filename}")
                return

            source_path = os.path.join(self.source_dir, filename)
            dest_path = os.path.join(self.dest_dir, filename)

            if os.path.exists(source_path):
                logger.info(f"Processing file: {filename}")

                # Copy the file to local directory
                shutil.copy2(source_path, dest_path)
                logger.info(f"Copied {filename} to {self.dest_dir}")

                # Delete from source
                os.remove(source_path)
                logger.info(f"Deleted {filename} from {self.source_dir}")

                # Record as processed
                self.processed_data[tracking_key] = {
                    "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "filename": filename,
                    "row_data": row.to_dict()
                }
            else:
                logger.warning(f"File not found in source: {source_path}")

        except Exception as e:
            logger.error(f"Error processing row {row.get('Sl no')}: {e}")

    def run_loop(self):
        """Run the fetcher every 5 minutes."""
        logger.info("Starting CSVFetcher loop (every 5 minutes)...")
        while True:
            self.fetch_and_process()
            time.sleep(300) # 300 seconds = 5 minutes

if __name__ == "__main__":
    import sys
    fetcher = CSVFetcher()
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        fetcher.fetch_and_process(once=True)
    else:
        fetcher.run_loop()
