import os
import pypowerwall
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

try:
    # Connect to the local Powerwall gateway
    pw = pypowerwall.Powerwall(
        host=os.environ.get("PW_HOST"),
        password=os.environ.get("PW_PASSWORD"),
        email=os.environ.get("PW_EMAIL"),
        timezone="America/Chicago"
    )

    # Fetch and print live data
    print("✅ Connection Successful!")
    print(f"Firmware Version: {pw.version()}")
    print(f"Battery Level: {pw.level():0.0f}%")
    print(f"Solar Power: {pw.solar()} W")
    print(f"Home Usage: {pw.home()} W")
    
except Exception as e:
    print(f"❌ Connection Failed: {e}")
