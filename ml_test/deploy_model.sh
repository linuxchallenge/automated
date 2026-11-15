#!/bin/sh
# Send a retrained model to the Pi:  sh ml_test/deploy_model.sh
# Commits ONLY auto_straddle/nifty_ml_model.json, pushes the current branch, then pulls on the Pi.
# The engine reloads the model file by itself at its next 15-minute check, so no restart is needed.
# The pull also brings any other commits already pushed to this branch; those take effect
# only when the engine is next restarted. You are shown them and asked before pulling.
set -e
cd "$(dirname "$0")/.."

PI=pitest@192.168.1.4
PI_REPO=automated
MODEL=auto_straddle/nifty_ml_model.json
BRANCH=$(git branch --show-current)

if [ -z "$(git status --porcelain -- "$MODEL")" ]; then
    echo "$MODEL has no changes to commit."
else
    git add "$MODEL"
    git commit -m "Retrain Nifty ML model $(date +%F)" -- "$MODEL"
fi
git push origin "$BRANCH"

echo "== commits the Pi will receive on $BRANCH:"
ssh "$PI" "cd $PI_REPO && git fetch -q origin $BRANCH && git log --oneline HEAD..origin/$BRANCH"
printf "Pull these on the Pi now? [y/N] "
read answer
if [ "$answer" = "y" ]; then
    ssh "$PI" "cd $PI_REPO && git pull --ff-only origin $BRANCH"
    echo "Pulled. The engine picks up the new model at its next 15-minute check."
else
    echo "Not pulled. Run 'git pull' on the Pi when ready."
fi
