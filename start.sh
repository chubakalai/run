#!/bin/bash
set -e

while true; do
    echo "=== Starting BF Bots at $(date) ==="
    
    python bfbot926.py BTC 6 0.15 --lev 50 --wnd 10 &
    PIDS=($!)
    echo "BTC bot started (PID: ${PIDS[0]})"

    python bfbot926.py ETH 6 0.20 --lev 30 --wnd 10 &
    PIDS+=($!)
    echo "ETH bot started (PID: ${PIDS[1]})"

    python bfbot926.py XRP 6 0.20 --lev 30 --wnd 10 &
    PIDS+=($!)
    echo "XRP bot started (PID: ${PIDS[2]})"

    python bfbot926.py SOL 6 0.20 --lev 30 --wnd 10 &
    PIDS+=($!)
    echo "SOL bot started (PID: ${PIDS[3]})"

    echo "=== Starting Web Server ==="
    python -m http.server 8080 &
    PIDS+=($!)
    echo "Web server started (PID: ${PIDS[4]})"

    echo "All 5 processes running. Next restart at midnight..."
    
    # Calculate seconds until next midnight
    current_hour=$(date +%H)
    current_minute=$(date +%M)
    current_second=$(date +%S)
    seconds_until_midnight=$(( (24 - current_hour - 1) * 3600 + (60 - current_minute - 1) * 60 + (60 - current_second) ))
    
    # Wait for either midnight or process failure
    if wait -n -t $seconds_until_midnight; then
        echo "A process exited unexpectedly at $(date) — restarting all"
        kill "${PIDS[@]}" 2>/dev/null
        sleep 2
    else
        echo "Midnight reached at $(date) — restarting all bots"
        kill "${PIDS[@]}" 2>/dev/null
        sleep 2
    fi
done
