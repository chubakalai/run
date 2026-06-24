#!/bin/bash
set -e

echo "=== Starting BF Bots ==="
python bfbot927.py BTC 12 0.15 --lev 50 --wnd 10 &
PIDS=($!)
echo "BTC bot started (PID: ${PIDS[0]})"

python bfbot927.py ETH 12 0.20 --lev 30 --wnd 10 &
PIDS+=($!)
echo "ETH bot started (PID: ${PIDS[1]})"

python bfbot927.py XRP 12 0.20 --lev 30 --wnd 10 &
PIDS+=($!)
echo "XRP bot started (PID: ${PIDS[2]})"

python bfbot927.py SOL 12 0.20 --lev 30 --wnd 10 &
PIDS+=($!)
echo "SOL bot started (PID: ${PIDS[3]})"

echo "=== Starting Web Server ==="
python -m http.server 8080 &
PIDS+=($!)
echo "Web server started (PID: ${PIDS[4]})"

echo "All 5 processes running. Watching..."

# Exit container if ANY process dies
wait -n
echo "A process exited unexpectedly — shutting down all"
kill "${PIDS[@]}" 2>/dev/null
exit 1
