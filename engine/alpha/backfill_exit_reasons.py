"""One-off script: re-classify trades with exit_reason='UNKNOWN'.

The raw `reason` column is preserved in Supabase.  This script re-runs
the updated _extract_exit_reason() logic (which now includes RATCHET)
against every closed trade currently tagged UNKNOWN and patches the
exit_reason column.

Usage:
    cd engine
    python -m alpha.backfill_exit_reasons          # dry-run (default)
    python -m alpha.backfill_exit_reasons --apply   # actually write to DB
"""

from __future__ import annotations

import argparse
import sys

from supabase import create_client

from alpha.config import config


# ── Exact copy of the updated extraction logic ──────────────────────────────

def _extract_exit_reason(reason: str) -> str:
    if not reason:
        return "UNKNOWN"
    upper = reason.upper()
    for kw in ("HARD_TP", "PROFIT_LOCK", "DEAD_MOMENTUM", "DECAY_EMERGENCY",
               "MANUAL_CLOSE", "SPOT_PULLBACK", "SPOT_DECAY", "SPOT_BREAKEVEN",
               "TRAIL", "RATCHET", "SL", "FLAT", "TIMEOUT",
               "BREAKEVEN", "REVERSAL", "PULLBACK", "DECAY", "SAFETY", "EXPIRY"):
        if kw in upper:
            return "MANUAL" if kw == "MANUAL_CLOSE" else kw
    direct = {
        "POSITION_GONE": "POSITION_GONE", "PHANTOM_CLEARED": "PHANTOM",
        "SL_EXCHANGE": "SL_EXCHANGE", "TP_EXCHANGE": "TP_EXCHANGE",
        "CLOSED_BY_EXCHANGE": "CLOSED_BY_EXCHANGE", "ORPHAN": "ORPHAN",
        "DUST": "DUST",
    }
    for key, val in direct.items():
        if key in upper:
            return val
    return "UNKNOWN"


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill UNKNOWN exit_reasons")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes to Supabase (default is dry-run)")
    args = parser.parse_args()

    url, key = config.supabase.url, config.supabase.key
    if not url or not key:
        print("ERROR: Supabase credentials not configured.")
        sys.exit(1)

    client = create_client(url, key)

    # Fetch all closed trades where exit_reason is UNKNOWN (or NULL)
    print("Fetching trades with exit_reason = 'UNKNOWN' …")
    result = (
        client.table("trades")
        .select("id, reason, exit_reason")
        .eq("status", "closed")
        .eq("exit_reason", "UNKNOWN")
        .execute()
    )
    rows = result.data or []
    print(f"Found {len(rows)} UNKNOWN trades.\n")

    reclassified = 0
    still_unknown = 0

    for row in rows:
        trade_id = row["id"]
        raw_reason = row.get("reason") or ""
        new_exit = _extract_exit_reason(raw_reason)

        if new_exit == "UNKNOWN":
            still_unknown += 1
            print(f"  [SKIP]  id={trade_id}  reason={raw_reason!r}  → still UNKNOWN")
            continue

        reclassified += 1
        print(f"  [FIX]   id={trade_id}  reason={raw_reason!r}  → {new_exit}")

        if args.apply:
            client.table("trades").update({"exit_reason": new_exit}).eq("id", trade_id).execute()

    print(f"\n{'='*60}")
    print(f"Reclassified:   {reclassified}")
    print(f"Still UNKNOWN:  {still_unknown}")
    if not args.apply and reclassified > 0:
        print(f"\n⚠  DRY RUN — no changes written. Re-run with --apply to commit.")
    elif args.apply and reclassified > 0:
        print(f"\n✓  {reclassified} trades updated in Supabase.")


if __name__ == "__main__":
    main()
