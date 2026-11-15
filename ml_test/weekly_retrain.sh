#!/bin/sh
# Weekly retrain of the Nifty ML model. Run on the laptop after Friday's close:
#   sh ml_test/weekly_retrain.sh
# 1. copies the Pi's option chain files (the Pi keeps only ~15 days, so do not skip weeks)
# 2. brings the Nifty price files up to date
# 3. trains and writes auto_straddle/nifty_ml_model.json
# Nothing is committed or sent to the Pi; review the output, then run deploy_model.sh.
set -e
cd "$(dirname "$0")"

PI=pitest@192.168.1.4
DATA=$HOME/temp/data_collection/data_collection
PY=../venv/bin/python

mkdir -p "$DATA/csv_pi"
echo "== 1/3 copying option chain files from the Pi"
scp -q -p "$PI:temp/data_collection/csv/options_chain_NIFTY_*.csv" \
          "$PI:temp/data_collection/csv/options_chain_BANKNIFTY_*.csv" "$DATA/csv_pi/"
echo "   $(ls "$DATA/csv_pi" | grep -c options_chain_NIFTY) Nifty days now in $DATA/csv_pi"

echo "== 2/3 updating Nifty price files"
$PY update_prices.py

echo "== 3/3 training"
$PY train_model.py "$DATA"

echo "Done. Review the lines above, then run: sh ml_test/deploy_model.sh"
