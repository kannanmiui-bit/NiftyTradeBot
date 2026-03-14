"""
generate_token.py — Generate a fresh Kite access token.

Run this ONCE every morning before starting the bot:
    python generate_token.py

Steps:
  1. Opens the Kite login URL in your browser
  2. You log in and are redirected to a URL containing `request_token=...`
  3. Paste that full URL here
  4. Script exchanges it for an access_token and updates your .env
"""

import os
import sys
import webbrowser
from urllib.parse import urlparse, parse_qs

from kiteconnect import KiteConnect
from dotenv import load_dotenv, set_key

load_dotenv()

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


def main():
    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        print("ERROR: Set KITE_API_KEY and KITE_API_SECRET in your .env file first.")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print("=" * 60)
    print("  Kite Access Token Generator")
    print("=" * 60)
    print(f"\nOpening login URL in your browser...")
    print(f"  {login_url}\n")

    try:
        webbrowser.open(login_url)
    except Exception:
        pass

    print("After logging in, Kite will redirect you to a URL like:")
    print("  https://127.0.0.1/?request_token=XXXXXX&action=login&status=success")
    print()
    redirected_url = input("Paste the full redirected URL here: ").strip()

    # Extract request_token
    parsed = urlparse(redirected_url)
    params = parse_qs(parsed.query)
    request_token = params.get("request_token", [None])[0]

    if not request_token:
        # Try extracting manually
        for part in redirected_url.split("&"):
            if part.startswith("request_token="):
                request_token = part.split("=", 1)[1]
                break

    if not request_token:
        print("\nERROR: Could not find request_token in the URL.")
        print("Make sure you pasted the full redirect URL.")
        sys.exit(1)

    print(f"\nExtracting request_token: {request_token[:10]}...")

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session["access_token"]
        user_name = session.get("user_name", "")
        user_id = session.get("user_id", "")
    except Exception as e:
        print(f"\nERROR generating session: {e}")
        sys.exit(1)

    # Update .env file
    set_key(ENV_FILE, "KITE_ACCESS_TOKEN", access_token)
    print(f"\nSuccess! Logged in as: {user_name} ({user_id})")
    print(f"Access token saved to .env: {access_token[:10]}...")
    print("\nYou can now run:")
    print("  python download_data.py")
    print("  python main.py --paper")


if __name__ == "__main__":
    main()
