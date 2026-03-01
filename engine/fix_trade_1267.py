"""Fix trade #1267 — fetch real exit data from Delta Exchange and update Supabase.

Run from engine/ directory where .env exists:
    python fix_trade_1267.py

What it does:
1. Loads .env for Supabase + Delta credentials
2. Fetches trade #1267 from Supabase
3. Queries Delta Exchange for recent XRP/USD closed orders/positions
4. Finds the matching fill and gets the real exit price
5. Recalculates P&L using the same calc_pnl function the bot uses
6. Updates the trade in Supabase with corrected data
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure engine/ is on path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import ccxt
from supabase import create_client


TRADE_ID = 1267
PAIR = "XRP/USD:USD"
ENTRY_PRICE = 1.3821
CONTRACTS = 31
LEVERAGE = 20
POSITION_TYPE = "long"
EXCHANGE_ID = "delta"
CONTRACT_SIZE = 1.0  # XRP = 1.0 per contract


async def main():
    # ── 1. Connect to Supabase ──
    sb_url = os.getenv("SUPABASE_URL")
    sb_key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not sb_url or not sb_key:
        print("ERROR: SUPABASE_URL or SUPABASE_KEY not set in .env")
        return
    sb = create_client(sb_url, sb_key)

    # ── 2. Fetch the trade from Supabase ──
    result = sb.table("trades").select("*").eq("id", TRADE_ID).execute()
    if not result.data:
        print(f"ERROR: Trade #{TRADE_ID} not found in Supabase")
        return
    trade = result.data[0]
    print(f"\n=== TRADE #{TRADE_ID} (current state) ===")
    for k in ("pair", "side", "entry_price", "exit_price", "amount", "leverage",
              "pnl", "pnl_pct", "status", "exit_reason", "closed_at"):
        print(f"  {k}: {trade.get(k)}")

    # ── 3. Connect to Delta Exchange ──
    delta_key = os.getenv("DELTA_API_KEY")
    delta_secret = os.getenv("DELTA_API_SECRET")
    if not delta_key or not delta_secret:
        print("ERROR: DELTA_API_KEY or DELTA_API_SECRET not set in .env")
        return

    delta = ccxt.delta({
        "apiKey": delta_key,
        "secret": delta_secret,
        "enableRateLimit": True,
    })

    # ── 4. Fetch recent closed orders for XRP/USD ──
    print(f"\nFetching recent XRP/USD orders from Delta...")
    try:
        # Try fetching closed orders
        orders = delta.fetch_closed_orders(PAIR, limit=50)
        print(f"Found {len(orders)} closed orders")

        # Find orders near our entry time and price
        matching = []
        for o in orders:
            price = o.get("average") or o.get("price") or 0
            amount = o.get("filled") or o.get("amount") or 0
            side = o.get("side", "")
            ts = o.get("datetime", "")
            status = o.get("status", "")
            # Look for sell orders (exit of our long) near our contract count
            if side == "sell" and abs(amount - CONTRACTS) <= 2:
                matching.append(o)
                print(f"  MATCH: {side} {amount} @ ${price} | {ts} | status={status}")
            elif abs(price - ENTRY_PRICE) < 0.01:
                print(f"  NEAR:  {side} {amount} @ ${price} | {ts} | status={status}")

    except Exception as e:
        print(f"fetch_closed_orders failed: {e}")
        orders = []
        matching = []

    # ── 5. Also try fetching trade history ──
    print(f"\nFetching recent XRP/USD trades from Delta...")
    try:
        trades = delta.fetch_my_trades(PAIR, limit=50)
        print(f"Found {len(trades)} trades")
        for t in trades[-10:]:  # last 10
            price = t.get("price", 0)
            amount = t.get("amount", 0)
            side = t.get("side", "")
            ts = t.get("datetime", "")
            fee = t.get("fee", {})
            print(f"  {side} {amount} @ ${price} | {ts} | fee={fee}")
    except Exception as e:
        print(f"fetch_my_trades failed: {e}")
        trades = []

    # ── 6. Determine exit price ──
    exit_price = None

    # From matching sell orders
    if matching:
        # Use the most recent matching sell
        best = matching[-1]
        exit_price = best.get("average") or best.get("price")
        print(f"\n>>> Using exit price from matching order: ${exit_price}")

    # From trade exit_price in DB (if it's nonzero)
    if not exit_price:
        db_exit = trade.get("exit_price")
        if db_exit and float(db_exit) > 0:
            exit_price = float(db_exit)
            print(f"\n>>> Using exit price from DB: ${exit_price}")

    if not exit_price:
        # Last resort: try to find from trade history
        sell_trades = [t for t in trades if t.get("side") == "sell"]
        if sell_trades:
            # Find sell trades with amount matching our position
            total_sell_amount = 0
            weighted_price = 0
            for t in sell_trades:
                total_sell_amount += t.get("amount", 0)
                weighted_price += t.get("price", 0) * t.get("amount", 0)
            if total_sell_amount > 0:
                exit_price = weighted_price / total_sell_amount
                print(f"\n>>> Using VWAP exit from sell trades: ${exit_price:.4f}")

    if not exit_price:
        print("\n!!! Could not determine exit price from any source.")
        print("    Enter manually (or press Enter to skip):")
        manual = input("    Exit price: $").strip()
        if manual:
            exit_price = float(manual)
        else:
            print("    Skipped. No update.")
            return

    # ── 7. Recalculate P&L ──
    from alpha.trade_executor import calc_pnl

    result_pnl = calc_pnl(
        entry_price=ENTRY_PRICE,
        exit_price=exit_price,
        amount=CONTRACTS,
        position_type=POSITION_TYPE,
        leverage=LEVERAGE,
        exchange_id=EXCHANGE_ID,
        pair=PAIR,
        entry_fee_rate=0.00059,  # Delta taker with GST
        exit_fee_rate=0.00059,
    )

    print(f"\n=== RECALCULATED P&L ===")
    print(f"  Entry:    ${ENTRY_PRICE}")
    print(f"  Exit:     ${exit_price}")
    print(f"  Gross:    ${result_pnl.gross_pnl:.4f}")
    print(f"  Fees:     ${result_pnl.entry_fee + result_pnl.exit_fee:.4f}")
    print(f"  Net P&L:  ${result_pnl.net_pnl:.4f}")
    print(f"  P&L %:    {result_pnl.pnl_pct:.2f}%")

    # ── 8. Confirm and update ──
    print(f"\nUpdate trade #{TRADE_ID} in Supabase? (y/n)")
    confirm = input("  > ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    update_data = {
        "exit_price": round(exit_price, 8),
        "pnl": round(result_pnl.net_pnl, 8),
        "pnl_pct": round(result_pnl.pnl_pct, 4),
    }
    print(f"  Updating: {update_data}")
    sb.table("trades").update(update_data).eq("id", TRADE_ID).execute()
    print(f"  DONE — trade #{TRADE_ID} updated.")


if __name__ == "__main__":
    asyncio.run(main())
