import subprocess
import os
import sys
import signal

# Define bot configurations
bots = [
    {"symbol": "BTC", "interval": 12, "threshold": 0.15, "lev": 50, "wnd": 10},
    {"symbol": "ETH", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
    {"symbol": "XRP", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
    {"symbol": "SOL", "interval": 12, "threshold": 0.20, "lev": 30, "wnd": 10},
]

processes = []

# Start each bot in the background
for bot in bots:
    cmd = [
        "python", "bfbot926.py",
        bot["symbol"],
        str(bot["interval"]),
        str(bot["threshold"]),
        "--lev", str(bot["lev"]),
        "--wnd", str(bot["wnd"])
    ]
    
    # Start process WITHOUT waiting (true background)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,  # or use subprocess.PIPE if you want output
        stderr=subprocess.DEVNULL,
        start_new_session=True      # Detach from parent process
    )
    processes.append((bot["symbol"], process))
    print(f"Started {bot['symbol']} bot (PID: {process.pid})")

print(f"\n{len(processes)} bots running in background. Script exiting...")
