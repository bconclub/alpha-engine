'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatShortDate, formatTimeAgo, cn } from '@/lib/utils';
import type { OptionsState, ActivityLogRow, OpenPosition } from '@/lib/types';

// Options-eligible assets (options_scalp only runs on BTC + ETH)
const OPTIONS_ASSETS = ['BTC', 'ETH'] as const;

function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

/** Format premium as $X.XXXX */
function fmtPrem(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toFixed(4)}`;
}

/** Format spot price ($98,450 for BTC, $2,780 for ETH) */
function fmtSpot(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

/** Format strike ($98,400 for BTC, $2,780 for ETH) */
function fmtStrike(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

/** How old is the last update in milliseconds? */
function ageMs(updatedAt: string | null): number {
  if (!updatedAt) return Infinity;
  return Date.now() - new Date(updatedAt).getTime();
}

/** Staleness check: is updated_at older than 2 minutes? */
function isStale(updatedAt: string | null): boolean {
  return ageMs(updatedAt) > 2 * 60 * 1000;
}

const OPTION_SYMBOL_RE = /\d{6}-\d+-[CP]/;

interface MergedPairState {
  asset: string;
  pair: string;
  state: OptionsState | null;
  recentEvents: ActivityLogRow[];
  stale: boolean;
  /** Fallback: open options trade from trades table (if options_state lacks position info) */
  openTrade: OpenPosition | null;
}

export function OptionsTracker() {
  const { optionsState, optionsLog, openPositions } = useSupabase();

  const pairStates = useMemo(() => {
    const results: MergedPairState[] = [];

    // Build a map of open options trades by base asset
    const optionTrades = new Map<string, OpenPosition>();
    for (const pos of (openPositions ?? [])) {
      if (pos.strategy === 'options_scalp' || OPTION_SYMBOL_RE.test(pos.pair)) {
        const asset = extractBaseAsset(pos.pair);
        optionTrades.set(asset, pos);
      }
    }

    for (const asset of OPTIONS_ASSETS) {
      const pair = `${asset}/USD:USD`;

      // Find options_state row for this pair
      const state = optionsState.find((s) => s.pair === pair) ?? null;

      // Get recent activity_log events for mini-log
      const assetEvents = optionsLog.filter((e) => {
        return extractBaseAsset(e.pair) === asset;
      });

      results.push({
        asset,
        pair,
        state,
        recentEvents: assetEvents.slice(0, 5),
        stale: isStale(state?.updated_at ?? null),
        openTrade: optionTrades.get(asset) ?? null,
      });
    }

    return results;
  }, [optionsState, optionsLog, openPositions]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Options Overview
        </h3>
        <span className="text-[9px] text-zinc-600 font-mono">BTC + ETH | 30s refresh</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {pairStates.map((ps) => (
          <PairCard key={ps.asset} ps={ps} />
        ))}
      </div>
    </div>
  );
}

// ─── Per-asset card ─────────────────────────────────────────

function PairCard({ ps }: { ps: MergedPairState }) {
  const s = ps.state;
  // Position is "live" if:
  //   1. options_state has position_side set AND data is fresh (< 5 min), OR
  //   2. There's an open options trade in the trades table (fallback for engine restart)
  const positionSideSet = s?.position_side != null;
  const dataAge = ageMs(s?.updated_at ?? null);
  const hasPositionFromState = positionSideSet && dataAge < 5 * 60 * 1000;
  const hasPositionFromTrades = ps.openTrade != null;
  const hasPosition = hasPositionFromState || hasPositionFromTrades;

  // Derive position info: prefer options_state, fallback to trades table
  const positionSide = s?.position_side ?? (
    hasPositionFromTrades
      ? (ps.openTrade!.pair.endsWith('-C') ? 'call' : ps.openTrade!.pair.endsWith('-P') ? 'put' : 'call')
      : null
  );
  const entryPremium = s?.entry_premium ?? (hasPositionFromTrades ? ps.openTrade!.entry_price : null);
  const currentPremium = s?.current_premium ?? (hasPositionFromTrades ? (ps.openTrade!.current_price ?? null) : null);
  const pnlPct = s?.pnl_pct ?? (hasPositionFromTrades ? (ps.openTrade!.current_pnl ?? null) : null);
  const pnlUsd = s?.pnl_usd ?? null;
  const positionStrike = s?.position_strike ?? null;
  const trailingActive = s?.trailing_active ?? (hasPositionFromTrades && ps.openTrade!.position_state === 'trailing');
  const highestPremium = s?.highest_premium ?? null;

  const isReady = (s?.signal_strength ?? 0) >= 3;
  const strength = s?.signal_strength ?? 0;

  return (
    <div
      className={cn(
        'bg-zinc-900/40 border rounded-lg p-3',
        hasPosition ? 'border-[#7c4dff]/40' : 'border-zinc-800/50',
      )}
    >
      {/* Header: asset name + staleness + updated time */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white">{ps.asset}</span>
          {s?.spot_price != null && (
            <span className="text-[10px] font-mono text-zinc-500">{fmtSpot(s.spot_price)}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {hasPosition && (
            <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-medium text-[#7c4dff] bg-[#7c4dff]/10 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[#7c4dff] animate-pulse" />
              {positionSide?.toUpperCase()} OPEN
            </span>
          )}
          {ps.stale ? (
            <span className="text-[8px] text-zinc-600 font-mono">STALE</span>
          ) : s?.updated_at ? (
            <span className="text-[8px] text-zinc-600 font-mono flex items-center gap-1">
              <span className="w-1 h-1 rounded-full bg-[#00c853]" />
              {formatTimeAgo(s.updated_at)}
            </span>
          ) : null}
        </div>
      </div>

      {/* No data state */}
      {!s ? (
        <div className="text-[10px] font-mono text-zinc-600 py-3 text-center">
          Waiting for engine data...
        </div>
      ) : (
        <>
          {/* Row 1: Expiry + ATM Strike */}
          <div className="grid grid-cols-2 gap-2 mb-2.5">
            <div>
              <div className="text-[9px] text-zinc-500 uppercase mb-0.5">Expiry</div>
              <div className="text-[10px] font-mono text-zinc-300 truncate">
                {s.expiry_label ?? '—'}
              </div>
            </div>
            <div>
              <div className="text-[9px] text-zinc-500 uppercase mb-0.5">ATM Strike</div>
              <div className="text-[10px] font-mono text-zinc-300">
                {fmtStrike(s.atm_strike)}
              </div>
            </div>
          </div>

          {/* Row 2: CALL / PUT premiums */}
          <div className="grid grid-cols-2 gap-2 mb-2.5">
            <div>
              <div className="text-[9px] text-zinc-500 uppercase mb-0.5">CALL Premium</div>
              <div className="text-[10px] font-mono text-[#00c853]">
                {fmtPrem(s.call_premium)}
              </div>
            </div>
            <div>
              <div className="text-[9px] text-zinc-500 uppercase mb-0.5">PUT Premium</div>
              <div className="text-[10px] font-mono text-[#ff1744]">
                {fmtPrem(s.put_premium)}
              </div>
            </div>
          </div>

          {/* Row 3: Signal strength bar */}
          <div className="mb-2.5">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[9px] text-zinc-500 uppercase w-14 shrink-0">Signal</span>
              <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: `${(strength / 4) * 100}%`,
                    backgroundColor: strength >= 3 ? '#7c4dff' : strength >= 1 ? '#ffd600' : '#71717a',
                  }}
                />
              </div>
              <span className={cn(
                'text-[10px] font-mono w-8 text-right',
                strength >= 3 ? 'text-[#7c4dff]' : strength >= 1 ? 'text-[#ffd600]' : 'text-zinc-600',
              )}>
                {strength}/4
              </span>
            </div>
            <div className="text-[9px] font-mono">
              {isReady ? (
                <span className={s.signal_side === 'long' ? 'text-[#00c853]' : 'text-[#ff1744]'}>
                  SIGNAL: {s.signal_side?.toUpperCase()} {strength}/4 ✔
                </span>
              ) : strength > 0 ? (
                <span className="text-[#ffd600]">
                  {s.signal_side === 'long' ? 'CALL' : s.signal_side === 'short' ? 'PUT' : '...'} building ({strength}/4)
                </span>
              ) : (
                <span className="text-zinc-600">Waiting for 3/4+ signal...</span>
              )}
            </div>
          </div>

          {/* Row 4: Active position or "None" */}
          {hasPosition ? (
            <div className="bg-[#7c4dff]/5 border border-[#7c4dff]/20 rounded p-2 mb-2">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] font-medium text-[#7c4dff] uppercase">
                  {positionSide?.toUpperCase()} Position
                </span>
                {trailingActive && (
                  <span className="flex items-center gap-1 text-[9px] font-mono text-[#00c853]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#00c853] animate-pulse" />
                    TRAILING
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[9px] font-mono text-zinc-400">
                {positionStrike != null && (
                  <div>
                    <span className="text-zinc-600">Strike </span>
                    {fmtStrike(positionStrike)}
                  </div>
                )}
                <div>
                  <span className="text-zinc-600">Entry </span>
                  {fmtPrem(entryPremium)}
                </div>
                <div>
                  <span className="text-zinc-600">Now </span>
                  {currentPremium != null ? fmtPrem(currentPremium) : <span className="text-zinc-600">updating...</span>}
                </div>
                <div>
                  <span className="text-zinc-600">P&L </span>
                  <span className={cn(
                    'font-medium',
                    (pnlPct ?? 0) >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
                  )}>
                    {pnlPct != null ? `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%` : '—'}
                    {pnlUsd != null && ` ($${pnlUsd >= 0 ? '+' : ''}${pnlUsd.toFixed(4)})`}
                  </span>
                </div>
              </div>
              {highestPremium != null && entryPremium != null && entryPremium > 0 && (
                <div className="text-[8px] font-mono text-zinc-600 mt-1">
                  Peak ${highestPremium.toFixed(4)} ({((highestPremium - entryPremium) / entryPremium * 100).toFixed(1)}%)
                </div>
              )}
            </div>
          ) : (
            <div className="text-[9px] font-mono text-zinc-600 mb-2">
              {s.last_exit_type ? (
                <span>
                  Last: {s.last_exit_type}
                  {s.last_exit_pnl_pct != null && (
                    <span className={cn(
                      'ml-1',
                      s.last_exit_pnl_pct >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
                    )}>
                      {s.last_exit_pnl_pct >= 0 ? '+' : ''}{s.last_exit_pnl_pct.toFixed(1)}%
                    </span>
                  )}
                  {s.last_exit_pnl_usd != null && (
                    <span className="text-zinc-600 ml-0.5">
                      (${s.last_exit_pnl_usd >= 0 ? '+' : ''}{s.last_exit_pnl_usd.toFixed(4)})
                    </span>
                  )}
                </span>
              ) : (
                <span>Position: None</span>
              )}
            </div>
          )}

          {/* Mini event log (last 3 options decisions from activity_log) */}
          {ps.recentEvents.length > 0 && (
            <div className="border-t border-zinc-800/50 pt-1.5 space-y-0.5">
              {ps.recentEvents.slice(0, 3).map((ev) => (
                <div key={ev.id} className="flex items-center gap-1.5">
                  <span className={cn(
                    'w-1 h-1 rounded-full shrink-0',
                    ev.event_type === 'options_entry' ? 'bg-[#7c4dff]'
                      : ev.event_type === 'options_exit' ? 'bg-[#7c4dff]/50'
                      : 'bg-zinc-700',
                  )} />
                  <span className="text-[8px] font-mono text-zinc-600 truncate">
                    {formatShortDate(ev.created_at)} {ev.event_type.replace('options_', '').toUpperCase()}
                    {ev.metadata?.pnl_pct != null && ` ${ev.metadata.pnl_pct >= 0 ? '+' : ''}${ev.metadata.pnl_pct.toFixed(1)}%`}
                    {ev.metadata?.option_type && ` ${ev.metadata.option_type.toUpperCase()}`}
                    {ev.metadata?.strike && ` $${ev.metadata.strike}`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
