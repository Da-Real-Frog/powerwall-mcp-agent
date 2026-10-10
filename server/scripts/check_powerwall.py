"""Checks the Powerwall connection and prints live readings, without starting the MCP server.

Run from the server/ directory:  uv run scripts/check_powerwall.py
"""
from dotenv import load_dotenv

from server import powerwall

load_dotenv()

try:
    pw = powerwall.get_connection()
    print("✅ Connection Successful!")
    print(f"Firmware Version: {pw.version()}")
    for name, value in powerwall.read_status(pw).items():
        print(f"{name}: {value}")
except Exception as e:
    print(f"❌ Connection Failed: {e}")
