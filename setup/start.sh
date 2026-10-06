#!/usr/bin/env bash
# Mac / Linux: build the page once, then serve it.
#   ./setup/start.sh            refresh (download + class-year scan) and serve
#   ./setup/start.sh --serve    skip the refresh, just serve
set -e
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}

if [ "$1" != "--serve" ]; then
  "$PY" autorefresh.py
fi

echo
echo "To refresh automatically every 3 hours, add this line with  crontab -e :"
echo "  0 */3 * * * cd \"$PWD\" && $PY autorefresh.py"
echo
exec "$PY" serve.py
