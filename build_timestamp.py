# build_timestamp.py — sets BUILD_TIMESTAMP at compile time
import time
import os

Import("env")

# Set BUILD_TIMESTAMP to current Unix timestamp
timestamp = str(int(time.time()))
env.Append(CPPDEFINES=[("BUILD_TIMESTAMP", timestamp)])
print(f"[build_timestamp] BUILD_TIMESTAMP={timestamp}")
