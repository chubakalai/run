#!/bin/bash
set -e

echo "=== Starting BF Bot ==="
python bfbot926.py BTC 12 0.15 --lev 50 --wnd 10 &
BOT_PID=$!
echo "Bot started (PID: $BOT_PID)"

echo "=== Starting Web Server ==="
# Serve nldca3.html on port 8080 (Fly.io default)
python -m http.server 8080 &
WEB_PID=$!
echo "Web server started (PID: $WEB_PID)"

# Keep container alive; exit if either process dies
wait -n
echo "A process exited — shutting down"
kill $BOT_PID $WEB_PID 2>/dev/null
exit 1
