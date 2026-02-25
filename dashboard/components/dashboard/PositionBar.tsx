'use client';

/**
 * Shared position visualization components used by both
 * LivePositions (homepage) and TradeTable (trades page).
 */

import { formatNumber, cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const TRAIL_ACTIVATION_PCT = 0.30; // must match engine TRAILING_ACTIVATE_PCT
export const DEFAULT_SL_PCT = 0.25;       // must match engine STOP_LOSS_PCT fallback

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a price with 4 decimals for cheap assets (XRP), 2 for the rest */
export function fmtPrice(value: number): string {
  const decimals = Math.abs(value) < 10 ? 4 : 2;
  return formatNumber(value, decimals);
}

/** Format a dollar P&L with 4 decimals when the absolute value is small */
export function fmtPnl(value: number): string {
  const decimals = Math.abs(value) < 10 ? 4 : 2;
  return `$${formatNumber(Math.abs(value), decimals)}`;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PositionDisplay {
  id: string;
  pair: string;
  pairShort: string;
  positionType: 'long' | 'short';
  entryPrice: number;
  currentPrice: number | null;
  contracts: number;
  leverage: number;
  pricePnlPct: number | null;    // raw price move %
  capitalPnlPct: number | null;  // leveraged capital return %
  pnlUsd: number | null;         // dollar P&L (gross, no fees)
  collateral: number | null;     // actual capital at risk
  duration: string;
  trailActive: boolean;
  trailStopPrice: number | null;
  peakPnlPct: number | null;     // highest price P&L % reached
  slPrice: number | null;
  tpPrice: number | null;
  exchange: string;
  isOption?: boolean;             // options trade flag
  optionSide?: 'CALL' | 'PUT' | null;  // CALL or PUT (null if not an option)
  // Momentum fade / dead momentum timer state
  fadeTimerActive?: boolean;
  fadeElapsed?: number | null;
  fadeRequired?: number | null;
  deadTimerActive?: boolean;
  deadElapsed?: number | null;
  deadRequired?: number | null;
}

// ---------------------------------------------------------------------------
// Position State Badge
// ---------------------------------------------------------------------------

export type PositionState = 'near_sl' | 'at_risk' | 'holding_loss' | 'holding_gain' | 'trailing';

export function getPositionState(pos: PositionDisplay): PositionState {
  const pnl = pos.pricePnlPct ?? 0;
  const peak = pos.peakPnlPct ?? 0;

  // TRAILING: only if trail genuinely active AND peak confirms it
  if (pos.trailActive && peak >= TRAIL_ACTIVATION_PCT) {
    return 'trailing';
  }

  // Compute SL distance to check "near SL"
  if (pos.slPrice != null && pos.currentPrice != null && pos.entryPrice > 0) {
    const slDist = Math.abs(pos.entryPrice - pos.slPrice);
    const currentDist = pos.positionType === 'long'
      ? pos.currentPrice - pos.slPrice
      : pos.slPrice - pos.currentPrice;
    // Near SL: within 30% of the SL distance from entry
    if (currentDist <= slDist * 0.30 && currentDist >= 0) {
      return 'near_sl';
    }
  }

  if (pnl < -0.15) return 'at_risk';
  if (pnl < 0) return 'holding_loss';
  return 'holding_gain';
}

/** Timer countdown badge for FADE or DEAD momentum */
function TimerBadge({ type, elapsed, required }: {
  type: 'fade' | 'dead';
  elapsed: number;
  required: number;
}) {
  const pct = Math.min(100, (elapsed / required) * 100);
  const isFade = type === 'fade';
  const icon = isFade ? '\u23F3' : '\uD83D\uDC80';
  const label = isFade ? 'FADE' : 'DEAD';
  const bgColor = isFade ? 'bg-amber-400/10' : 'bg-[#ff1744]/10';
  const textColor = isFade ? 'text-amber-400' : 'text-[#ff1744]';
  const barColor = isFade ? 'bg-amber-400' : 'bg-[#ff1744]';

  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', bgColor, textColor)}>
      <span>{icon}</span>
      <span>{label}</span>
      <span className="font-mono">{elapsed}/{required}s</span>
      <span className="relative w-6 h-1.5 rounded-full bg-zinc-700 overflow-hidden ml-0.5">
        <span
          className={cn('absolute top-0 left-0 h-full rounded-full transition-all duration-1000', barColor)}
          style={{ width: `${pct}%` }}
        />
      </span>
    </span>
  );
}

export function StateBadge({ state, trailStopPrice, pos }: {
  state: PositionState;
  trailStopPrice: number | null;
  entryPrice?: number;
  pos?: PositionDisplay;
}) {
  // Timer badges — show alongside the state badge
  const timerBadge = pos?.fadeTimerActive && pos.fadeElapsed != null && pos.fadeRequired != null
    ? <TimerBadge type="fade" elapsed={pos.fadeElapsed} required={pos.fadeRequired} />
    : pos?.deadTimerActive && pos.deadElapsed != null && pos.deadRequired != null
    ? <TimerBadge type="dead" elapsed={pos.deadElapsed} required={pos.deadRequired} />
    : null;

  let stateBadge: React.ReactNode;

  switch (state) {
    case 'near_sl':
      stateBadge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[#ff1744]/15 text-[#ff1744]">
          <span className="w-1.5 h-1.5 rounded-full bg-[#ff1744] animate-pulse" />
          NEAR SL
        </span>
      );
      break;
    case 'at_risk':
      stateBadge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[#ff1744]/10 text-[#ff1744]">
          AT RISK
        </span>
      );
      break;
    case 'holding_loss':
      stateBadge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-400/10 text-amber-400">
          HOLDING
        </span>
      );
      break;
    case 'holding_gain':
      stateBadge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[#00c853]/10 text-[#00c853]">
          HOLDING
        </span>
      );
      break;
    case 'trailing':
      stateBadge = (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-[#00c853]/10 text-[#00c853]">
          <span className="w-1.5 h-1.5 rounded-full bg-[#00c853] animate-pulse" />
          TRAILING
          {trailStopPrice != null && (
            <span className="text-zinc-400 font-normal ml-0.5">
              @${fmtPrice(trailStopPrice)}
            </span>
          )}
        </span>
      );
      break;
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      {stateBadge}
      {timerBadge}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Position Range Bar (SL <- Entry -> Peak/TP)
// ---------------------------------------------------------------------------

export function PositionRangeBar({ pos, compact = false }: { pos: PositionDisplay; compact?: boolean }) {
  const pnl = pos.pricePnlPct ?? 0;
  const entry = pos.entryPrice;
  const current = pos.currentPrice;
  const peak = pos.peakPnlPct ?? 0;

  if (current == null || entry <= 0) return null;

  // Compute SL price (from DB or estimate)
  const slPrice = pos.slPrice ?? (
    pos.positionType === 'long'
      ? entry * (1 - DEFAULT_SL_PCT / 100)
      : entry * (1 + DEFAULT_SL_PCT / 100)
  );

  // Range: SL distance below entry, peak/trail above entry
  const slDistPct = DEFAULT_SL_PCT; // distance from entry to SL in %
  const peakPct = Math.max(peak, Math.abs(pnl), 0.05); // at least 0.05 to avoid zero range

  // Total range: SL side + profit side
  const totalRange = slDistPct + Math.max(peakPct, TRAIL_ACTIVATION_PCT);

  // Entry position as % of total bar width (SL is at 0%, entry is partway)
  const entryPos = (slDistPct / totalRange) * 100;

  // Current price position on the bar
  const currentPos = ((slDistPct + pnl) / totalRange) * 100;
  const clampedCurrentPos = Math.max(0, Math.min(100, currentPos));

  // Trail activation line position
  const trailLinePos = ((slDistPct + TRAIL_ACTIVATION_PCT) / totalRange) * 100;

  // Trail stop position (if active)
  let trailStopPos: number | null = null;
  if (pos.trailActive && pos.trailStopPrice != null && entry > 0) {
    const trailStopPnl = pos.positionType === 'long'
      ? ((pos.trailStopPrice - entry) / entry) * 100
      : ((entry - pos.trailStopPrice) / entry) * 100;
    trailStopPos = Math.max(0, Math.min(100, ((slDistPct + trailStopPnl) / totalRange) * 100));
  }

  // Fill bar: from entry to current
  const fillLeft = Math.min(entryPos, clampedCurrentPos);
  const fillWidth = Math.abs(clampedCurrentPos - entryPos);
  const isProfit = pnl >= 0;

  return (
    <div className="w-full">
      {/* Bar with markers */}
      <div className="relative h-2.5 bg-zinc-800 rounded-full overflow-hidden">
        {/* SL zone background (left side, subtle red) */}
        <div
          className="absolute top-0 bottom-0 bg-[#ff1744]/8 rounded-l-full"
          style={{ left: 0, width: `${entryPos}%` }}
        />

        {/* Fill bar: current P&L relative to entry */}
        <div
          className={cn(
            'absolute top-0 bottom-0 transition-all duration-500 rounded-full',
            isProfit ? 'bg-[#00c853]' : 'bg-[#ff1744]',
          )}
          style={{ left: `${fillLeft}%`, width: `${fillWidth}%` }}
        />

        {/* Entry line */}
        <div
          className="absolute top-0 bottom-0 w-px bg-zinc-500"
          style={{ left: `${entryPos}%` }}
        />

        {/* Trail activation line (dashed) */}
        <div
          className="absolute top-0 bottom-0 w-px bg-[#00c853]/30"
          style={{ left: `${Math.min(trailLinePos, 100)}%` }}
        />

        {/* Trail stop line (if active) */}
        {trailStopPos != null && (
          <div
            className="absolute top-0 bottom-0 w-px bg-[#ffd600]"
            style={{ left: `${trailStopPos}%` }}
          />
        )}

        {/* Current price marker dot */}
        <div
          className={cn(
            'absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full border border-zinc-900 z-10 transition-all duration-500',
            isProfit ? 'bg-[#00c853]' : 'bg-[#ff1744]',
          )}
          style={{ left: `${clampedCurrentPos}%`, marginLeft: '-4px' }}
        />
      </div>

      {/* Labels below bar */}
      {compact ? (
        <div className="flex justify-between mt-0.5">
          <span className="text-[9px] font-mono text-[#ff1744]/70 leading-none">
            SL
          </span>
          <span className="text-[9px] font-mono text-zinc-500 leading-none">
            Entry
          </span>
          {pos.trailActive && pos.trailStopPrice != null ? (
            <span className="text-[9px] font-mono text-[#ffd600]/70 leading-none">
              Trail
            </span>
          ) : (
            <span className="text-[9px] font-mono text-zinc-600 leading-none">
              {peak > 0 ? `+${peak.toFixed(2)}%` : `+${TRAIL_ACTIVATION_PCT}%`}
            </span>
          )}
        </div>
      ) : (
        <div className="flex justify-between mt-0.5">
          <span className="text-[9px] font-mono text-[#ff1744]/70 leading-none truncate">
            SL ${fmtPrice(slPrice)}
          </span>
          <span className="text-[9px] font-mono text-zinc-500 leading-none shrink-0 mx-1">
            Entry
          </span>
          {pos.trailActive && pos.trailStopPrice != null ? (
            <span className="text-[9px] font-mono text-[#ffd600]/70 leading-none truncate text-right">
              Trail ${fmtPrice(pos.trailStopPrice)}
            </span>
          ) : (
            <span className="text-[9px] font-mono text-zinc-600 leading-none text-right">
              {peak > 0 ? `+${peak.toFixed(2)}%` : `+${TRAIL_ACTIVATION_PCT}%`}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
