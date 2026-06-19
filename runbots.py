import subprocess
import os
import sys
import signal
import time

# Define bot configurations
bots = [
    {"symbol": "BTC", "interval": 12, "threshold": 0.15, "lev": 50, "wnd": 10},
    {"symbol": "ETH", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
    {"symbol": "XRP", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
    {"symbol": "SOL", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
]

processes = []

def cleanup(signum, frame):
    """Kill all bots on exit"""
    print("\nShutting down bots...")
    for symbol, process in processes:
        process.kill()
    sys.exit(0)

# Handle graceful shutdown
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# Start each bot
for bot in bots:
    cmd = [
        "python", "bfbot926.py",
        bot["symbol"],
        str(bot["interval"]),
        str(bot["threshold"]),
        "--lev", str(bot["lev"]),
        "--wnd", str(bot["wnd"])
    ]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    processes.append((bot["symbol"], process))
    print(f"Started {bot['symbol']} bot (PID: {process.pid})")

print(f"\n{len(processes)} bots running. Monitoring...")

# Keep the main process alive
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    cleanup(None, None)
