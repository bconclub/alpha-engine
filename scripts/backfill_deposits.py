"""Fetch deposit/transfer history from Delta Exchange India and backfill Supabase.

Delta India uses INR deposits. This script:
1. Connects to Delta via ccxt (same .env as the bot)
2. Fetches wallet transaction history (deposits)
3. Converts INR → USD using the bot's INR rate
4. Inserts into Supabase `deposits` table (skips duplicates)

Usage (run from engine/ directory where .env and venv live):
    cd engine
    python ../scripts/backfill_deposits.py          # dry-run (default)
    python ../scripts/backfill_deposits.py --apply  # actually write to DB
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure engine/ is on sys.path so alpha.config works
engine_dir = Path(__file__).resolve().parent.parent / "engine"
if str(engine_dir) not in sys.path:
    sys.path.insert(0, str(engine_dir))

import aiohttp
import ccxt.async_support as ccxt
from supabase import create_client

from alpha.config import config

# INR→USD rate (same as dashboard uses)
INR_RATE = 85.0  # approximate — adjust if needed


async def fetch_delta_deposits() -> list[dict]:
    """Fetch deposit history from Delta Exchange India."""
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=True)
    )
    exchange = ccxt.delta({
        "apiKey": config.delta.api_key,
        "secret": config.delta.secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
        "session": session,
    })
    exchange.urls["api"] = {
        "public": config.delta.base_url,
        "private": config.delta.base_url,
    }

    deposits = []

    try:
        # Method 1: Try fetch_deposits (standard ccxt)
        try:
            raw = await exchange.fetch_deposits()
            for d in raw:
                deposits.append({
                    "created_at": d.get("datetime") or d.get("timestamp"),
                    "exchange": "delta",
                    "amount_inr": float(d.get("amount", 0)),
                    "amount": float(d.get("amount", 0)) / INR_RATE,
                    "currency": d.get("currency", "INR"),
                    "status": d.get("status", "ok"),
                    "tx_id": d.get("id") or d.get("txid"),
                })
            print(f"[fetch_deposits] Found {len(deposits)} deposits")
        except Exception as e:
            print(f"[fetch_deposits] Not supported or failed: {e}")

        # Method 2: Try fetch_ledger (broader — includes deposits, withdrawals, etc.)
        if not deposits:
            try:
                ledger = await exchange.fetch_ledger()
                for entry in ledger:
                    etype = (entry.get("type") or "").lower()
                    if etype in ("deposit", "transfer", "credit", "funding"):
                        amount = float(entry.get("amount", 0))
                        if amount > 0:
                            deposits.append({
                                "created_at": entry.get("datetime") or entry.get("timestamp"),
                                "exchange": "delta",
                                "amount_inr": amount,
                                "amount": amount / INR_RATE,
                                "currency": entry.get("currency", "INR"),
                                "status": "ok",
                                "tx_id": entry.get("id"),
                            })
                print(f"[fetch_ledger] Found {len(deposits)} deposit entries")
            except Exception as e:
                print(f"[fetch_ledger] Not supported or failed: {e}")

        # Method 3: Try Delta's native API for wallet transactions
        if not deposits:
            try:
                print("[native API] Trying Delta wallet transactions endpoint...")
                response = await exchange.privateGetWalletTransactions()
                txns = response if isinstance(response, list) else response.get("result", response.get("data", []))
                if isinstance(txns, dict):
                    txns = txns.get("result", [])
                for tx in txns:
                    tx_type = str(tx.get("transaction_type", "") or tx.get("type", "")).lower()
                    if tx_type in ("deposit", "credit", "transfer", "fund", "add"):
                        amount = abs(float(tx.get("amount", 0) or tx.get("balance_change", 0)))
                        if amount > 0:
                            ts = tx.get("created_at") or tx.get("timestamp") or tx.get("time")
                            deposits.append({
                                "created_at": ts,
                                "exchange": "delta",
                                "amount_inr": amount,
                                "amount": amount / INR_RATE,
                                "currency": tx.get("currency", "INR"),
                                "status": "ok",
                                "tx_id": tx.get("id"),
                            })
                print(f"[native API] Found {len(deposits)} deposit entries")
            except Exception as e:
                print(f"[native API] Failed: {e}")

        # Method 4: Try raw REST call to list all wallet transactions
        if not deposits:
            try:
                print("[raw API] Trying /v2/wallet/transactions ...")
                response = await exchange.privateGetV2WalletTransactions({"page_size": 100})
                print(f"[raw API] Response keys: {list(response.keys()) if isinstance(response, dict) else type(response)}")
                txns = response if isinstance(response, list) else response.get("result", [])
                for tx in (txns if isinstance(txns, list) else []):
                    print(f"  tx: {tx}")
                    amount = abs(float(tx.get("amount", 0)))
                    if amount > 0:
                        deposits.append({
                            "created_at": tx.get("created_at"),
                            "exchange": "delta",
                            "amount_inr": amount,
                            "amount": amount / INR_RATE,
                            "currency": "INR",
                            "status": "ok",
                            "tx_id": tx.get("id"),
                        })
                print(f"[raw API] Found {len(deposits)} entries")
            except Exception as e:
                print(f"[raw API] Failed: {e}")

    finally:
        await exchange.close()
        await session.close()

    return deposits


def backfill_supabase(deposits: list[dict], apply: bool = False):
    """Insert deposits into Supabase."""
    if not deposits:
        print("\nNo deposits found to backfill.")
        return

    client = create_client(config.supabase.url, config.supabase.key)

    # Fetch existing to avoid duplicates
    existing = client.table("deposits").select("created_at,amount").execute()
    existing_set = set()
    for row in existing.data:
        existing_set.add((row["created_at"], float(row["amount"])))

    new_deposits = []
    for d in deposits:
        # Normalize timestamp
        ts = d["created_at"]
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc).isoformat()

        amount_usd = round(d["amount"], 8)
        amount_inr = round(d["amount_inr"], 2)

        if (ts, amount_usd) in existing_set:
            print(f"  SKIP (exists): {ts}  ${amount_usd:.2f}  (₹{amount_inr:,.2f})")
            continue

        new_deposits.append({
            "created_at": ts,
            "exchange": "delta",
            "amount": amount_usd,
            "amount_inr": amount_inr,
            "notes": f"Backfilled from Delta API",
        })

    print(f"\n{'=' * 60}")
    print(f"Found {len(deposits)} deposits from Delta API")
    print(f"Already in DB: {len(deposits) - len(new_deposits)}")
    print(f"New to insert: {len(new_deposits)}")
    print(f"{'=' * 60}")

    for d in new_deposits:
        print(f"  {'WILL INSERT' if apply else 'DRY-RUN'}: "
              f"{d['created_at']}  ${d['amount']:.2f}  (₹{d['amount_inr']:,.2f})")

    if apply and new_deposits:
        result = client.table("deposits").insert(new_deposits).execute()
        print(f"\n✅ Inserted {len(result.data)} deposits into Supabase")
    elif not apply and new_deposits:
        print(f"\n⚠️  Dry run — use --apply to actually insert")


async def main():
    parser = argparse.ArgumentParser(description="Backfill Delta deposits into Supabase")
    parser.add_argument("--apply", action="store_true", help="Actually insert (default is dry-run)")
    args = parser.parse_args()

    print("Fetching deposit history from Delta Exchange India...")
    print(f"API endpoint: {config.delta.base_url}")
    print(f"INR→USD rate: {INR_RATE}")
    print()

    deposits = await fetch_delta_deposits()
    backfill_supabase(deposits, apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
