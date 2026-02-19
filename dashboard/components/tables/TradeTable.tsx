'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import type { Trade, Strategy, Exchange, PositionType } from '@/lib/types';
import {
  formatCurrency,
  formatPrice,
  formatPnL,
  formatPercentage,
  formatDate,
  formatDuration,
  cn,
  getPnLColor,
  tradesToCSV,
  getExchangeLabel,
  getExchangeColor,
  getPositionTypeLabel,
  getPositionTypeColor,
  formatLeverage,
  getStrategyLabel,
  getStrategyBadgeVariant,
} from '@/lib/utils';
import { Badge } from '@/components/ui/Badge';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { useLivePrices } from '@/hooks/useLivePrices';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey = keyof Pick<
  Trade,
  'timestamp' | 'pair' | 'side' | 'price' | 'amount' | 'strategy' | 'pnl' | 'pnl_pct' | 'status' | 'exchange' | 'position_type' | 'leverage' | 'gross_pnl'
>;

type SortDirection = 'asc' | 'desc';

type PnLFilter = 'all' | 'profit' | 'loss';

interface TradeTableProps {
  trades: Trade[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STRATEGIES: Strategy[] = ['scalp', 'options_scalp'];
const EXCHANGES: { label: string; value: Exchange | 'All' }[] = [
  { label: 'All', value: 'All' },
  { label: 'Delta', value: 'delta' },
];
const POSITION_TYPES: { label: string; value: PositionType | 'All' }[] = [
  { label: 'All', value: 'All' },
  { label: 'Spot', value: 'spot' },
  { label: 'Long', value: 'long' },
  { label: 'Short', value: 'short' },
];
const PNL_OPTIONS: { label: string; value: PnLFilter }[] = [
  { label: 'All', value: 'all' },
  { label: 'Profit', value: 'profit' },
  { label: 'Loss', value: 'loss' },
];
const TRADES_PER_PAGE = 50;

// Delta contract sizes (must match engine/alpha/trade_executor.py)
const DELTA_CONTRACT_SIZE: Record<string, number> = {
  'BTC/USD:USD': 0.001,
  'ETH/USD:USD': 0.01,
  'SOL/USD:USD': 1.0,
  'XRP/USD:USD': 1.0,
};

type ColumnDef = { key: string; label: string; align?: 'right' };

const COLUMNS: ColumnDef[] = [
  // ── Frozen columns (sticky left) ──
  { key: 'pair', label: 'Pair' },
  { key: 'position_type', label: 'Type' },
  { key: 'leverage', label: 'Lev', align: 'right' },
  { key: 'price', label: 'Entry', align: 'right' },
  // ── Scrollable columns ──
  { key: 'id', label: 'ID' },
  { key: 'timestamp', label: 'Date' },
  { key: 'exchange', label: 'Exchange' },
  { key: 'exit_price', label: 'Exit', align: 'right' },
  { key: 'amount', label: 'Contracts', align: 'right' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'setup_type', label: 'Setup' },
  { key: 'gross_pnl', label: 'Gross P&L', align: 'right' },
  { key: 'fees', label: 'Fees', align: 'right' },
  { key: 'pnl', label: 'Net P&L', align: 'right' },
  { key: 'pnl_pct', label: 'P&L %', align: 'right' },
  { key: 'hold_time', label: 'Hold Time', align: 'right' },
  { key: 'sl_price', label: 'SL', align: 'right' },
  { key: 'trail_info', label: 'Trail' },
  { key: 'peak_info', label: 'Peak', align: 'right' },
  { key: 'exit_reason', label: 'Exit' },
  { key: 'status', label: 'Status' },
];

// Frozen column sticky offsets (must match actual rendered widths)
const STICKY_COLS: Record<string, string> = {
  pair: 'left-0',
  position_type: 'left-[100px]',
  leverage: 'left-[180px]',
  price: 'left-[240px]',
};
const LAST_STICKY_COL = 'price';

// Setup type badge colors
const SETUP_COLORS: Record<string, { bg: string; text: string }> = {
  VWAP_RECLAIM:   { bg: 'bg-blue-500/10',   text: 'text-blue-400' },
  RSI_OVERRIDE:   { bg: 'bg-purple-500/10',  text: 'text-purple-400' },
  MOMENTUM_BURST: { bg: 'bg-orange-500/10',  text: 'text-orange-400' },
  MEAN_REVERT:    { bg: 'bg-cyan-500/10',    text: 'text-cyan-400' },
  TREND_CONT:     { bg: 'bg-emerald-500/10', text: 'text-emerald-400' },
  BB_SQUEEZE:     { bg: 'bg-red-500/10',     text: 'text-red-400' },
  LIQ_SWEEP:      { bg: 'bg-pink-500/10',    text: 'text-pink-400' },
  FVG_FILL:       { bg: 'bg-indigo-500/10',  text: 'text-indigo-400' },
  VOL_DIVERGENCE:  { bg: 'bg-teal-500/10',    text: 'text-teal-400' },
  MULTI_SIGNAL:   { bg: 'bg-yellow-500/10',  text: 'text-yellow-400' },
  MIXED:          { bg: 'bg-zinc-500/10',    text: 'text-zinc-400' },
};

function getSetupLabel(setup?: string): string {
  if (!setup) return '—';
  const labels: Record<string, string> = {
    VWAP_RECLAIM: 'VWAP',
    RSI_OVERRIDE: 'RSI OVR',
    MOMENTUM_BURST: 'MOM',
    MEAN_REVERT: 'REVERT',
    TREND_CONT: 'TREND',
    BB_SQUEEZE: 'SQUEEZE',
    LIQ_SWEEP: 'SWEEP',
    FVG_FILL: 'FVG',
    VOL_DIVERGENCE: 'VOL DIV',
    MULTI_SIGNAL: 'MULTI',
    MIXED: 'MIXED',
  };
  return labels[setup] ?? setup;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getStatusBadgeVariant(status: Trade['status']) {
  const map: Record<Trade['status'], 'success' | 'danger' | 'default'> = {
    open: 'success',
    closed: 'default',
    cancelled: 'danger',
  };
  return map[status];
}

function compareTrades(a: Trade, b: Trade, key: string, dir: SortDirection): number {
  let aVal: string | number = (a[key as keyof Trade] as string | number | undefined | null) ?? 0;
  let bVal: string | number = (b[key as keyof Trade] as string | number | undefined | null) ?? 0;

  if (key === 'timestamp') {
    aVal = new Date(aVal as string).getTime();
    bVal = new Date(bVal as string).getTime();
  }

  if (typeof aVal === 'string' && typeof bVal === 'string') {
    aVal = aVal.toLowerCase();
    bVal = bVal.toLowerCase();
  }

  if (aVal < bVal) return dir === 'asc' ? -1 : 1;
  if (aVal > bVal) return dir === 'asc' ? 1 : -1;
  return 0;
}

/** Get color for exit reason — expanded palette */
function getExitReasonColor(reason: string): string {
  const upper = reason.toUpperCase();
  // Green: profit exits
  if (['TRAIL', 'TP', 'HARD_TP', 'TP_EXCHANGE', 'PROFIT_LOCK'].includes(upper)) return 'text-emerald-400';
  // Blue: manual
  if (upper === 'MANUAL') return 'text-blue-400';
  // Red: stop loss
  if (upper === 'SL' || upper === 'SL_EXCHANGE') return 'text-red-400';
  // Yellow: conditional exits
  if (['REVERSAL', 'PULLBACK', 'DECAY', 'DECAY_EMERGENCY', 'SPOT_PULLBACK', 'SPOT_DECAY', 'SPOT_BREAKEVEN'].includes(upper)) return 'text-yellow-400';
  // Orange: external/phantom
  if (upper === 'PHANTOM' || upper === 'POSITION_GONE' || upper === 'CLOSED_BY_EXCHANGE') return 'text-orange-400';
  // Gray: neutral exits
  if (['FLAT', 'TIMEOUT', 'BREAKEVEN', 'SAFETY', 'DUST', 'ORPHAN', 'EXPIRY'].includes(upper)) return 'text-zinc-500';
  return 'text-zinc-500';
}

/** Parse exit reason from trade reason field (fallback for older trades without exit_reason column) */
function parseExitReason(reason?: string | null): string | null {
  if (!reason) return null;
  const upper = reason.toUpperCase().trim();
  // Check from most specific to least (HARD_TP before TP)
  const keywords = ['HARD_TP', 'PROFIT_LOCK', 'DECAY_EMERGENCY', 'MANUAL_CLOSE',
    'SPOT_PULLBACK', 'SPOT_DECAY', 'SPOT_BREAKEVEN',
    'TRAIL', 'TP', 'SL', 'FLAT', 'TIMEOUT', 'BREAKEVEN', 'REVERSAL', 'PULLBACK',
    'DECAY', 'SAFETY', 'EXPIRY'];
  for (const kw of keywords) {
    if (upper.includes(kw)) return kw === 'MANUAL_CLOSE' ? 'MANUAL' : kw;
  }
  // Direct matches
  const direct: Record<string, string> = {
    'PHANTOM_CLEARED': 'PHANTOM', 'SL_EXCHANGE': 'SL_EXCHANGE',
    'TP_EXCHANGE': 'TP_EXCHANGE', 'CLOSED_BY_EXCHANGE': 'CLOSED_BY_EXCHANGE',
    'POSITION_GONE': 'POSITION_GONE', 'DUST_UNSELLABLE': 'DUST',
    'ORPHAN_CLOSED': 'ORPHAN', 'ORPHAN_STRATEGY_REMOVED': 'ORPHAN',
    'POSITION_NOT_FOUND_ON_RESTART': 'POSITION_GONE',
  };
  for (const [key, val] of Object.entries(direct)) {
    if (upper.includes(key)) return val;
  }
  if (reason.length <= 10) return reason.toUpperCase();
  return null;
}

/** Get exit reason: prefer exit_reason column, fall back to parsing reason field */
function getExitReason(trade: Trade): string | null {
  if (trade.exit_reason) return trade.exit_reason;
  return parseExitReason(trade.reason);
}

/** Calculate hold time in seconds for a trade */
function calcHoldSeconds(trade: Trade, now: number): number {
  const openedMs = new Date(trade.timestamp).getTime();
  if (trade.status !== 'open' && trade.closed_at) {
    const closedMs = new Date(trade.closed_at).getTime();
    return Math.max(0, (closedMs - openedMs) / 1000);
  }
  // Open trade: use current time
  return Math.max(0, (now - openedMs) / 1000);
}

/** Live hold-time cell for open trades — ticks via shared `now` timestamp */
function HoldTimeCell({ trade, now }: { trade: Trade; now: number }) {
  if (trade.status !== 'open') {
    // Closed trade: static duration
    if (trade.closed_at) {
      return (
        <span className="font-mono text-zinc-400 text-xs">
          {formatDuration(calcHoldSeconds(trade, now))}
        </span>
      );
    }
    return <span className="text-zinc-600">&mdash;</span>;
  }
  // Open trade: live counter
  const seconds = calcHoldSeconds(trade, now);
  return (
    <span className="font-mono text-xs text-zinc-200">
      <span className="text-red-500 mr-1">&#x1F534;</span>
      {formatDuration(seconds)}
    </span>
  );
}

/** Extract base asset from a pair string, e.g. "SOL/USD:USD" → "SOL" */
function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

/** Clean pair name for display: "ETH/USD:USD" → "ETH/USD" */
function displayPair(pair: string): string {
  return pair.replace(/:USD$/, '');
}

/**
 * Calculate unrealized P&L for an open trade using the latest market price.
 * Returns { pnl, pnl_pct } or null if we can't calculate.
 */
function calcUnrealizedPnL(
  trade: Trade,
  currentPrice: number | null,
): { pnl: number; pnl_pct: number } | null {
  if (currentPrice == null || currentPrice <= 0) return null;
  if (trade.status !== 'open') return null;

  const entryPrice = trade.price;
  const contracts = trade.amount;
  if (!entryPrice || !contracts) return null;

  // Get coin amount from contracts
  let coinAmount = contracts;
  if (trade.exchange === 'delta') {
    const contractSize = DELTA_CONTRACT_SIZE[trade.pair] ?? 1.0;
    coinAmount = contracts * contractSize;
  }

  // Calculate gross P&L
  let grossPnl: number;
  if (trade.position_type === 'short') {
    grossPnl = (entryPrice - currentPrice) * coinAmount;
  } else {
    grossPnl = (currentPrice - entryPrice) * coinAmount;
  }

  // P&L % against collateral
  const notional = entryPrice * coinAmount;
  const leverage = trade.leverage > 1 ? trade.leverage : 1;
  const collateral = notional / leverage;
  const pnlPct = collateral > 0 ? (grossPnl / collateral) * 100 : 0;

  return { pnl: grossPnl, pnl_pct: pnlPct };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TradeTable({ trades }: TradeTableProps) {
  const { strategyLog } = useSupabase();
  const hasOpenTrades = trades.some((t) => t.status === 'open');
  const livePrices = useLivePrices(hasOpenTrades);

  // -- Scroll sync refs for top + bottom scrollbars ------------------------
  const topScrollRef = useRef<HTMLDivElement>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);

  // -- Dynamic top scrollbar width (matches actual table scroll width) -----
  const [tableScrollWidth, setTableScrollWidth] = useState(2100);
  useEffect(() => {
    const el = tableScrollRef.current;
    if (!el) return;
    const update = () => setTableScrollWidth(el.scrollWidth);
    update(); // initial
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // -- Live timer for open trade hold times --------------------------------
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const hasOpen = trades.some((t) => t.status === 'open');
    if (!hasOpen) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [trades]);

  // -- Filter state ---------------------------------------------------------
  const [strategyFilter, setStrategyFilter] = useState<Strategy | 'All'>('All');
  const [exchangeFilterLocal, setExchangeFilterLocal] = useState<Exchange | 'All'>('All');
  const [positionTypeFilter, setPositionTypeFilter] = useState<PositionType | 'All'>('All');
  const [pnlFilter, setPnlFilter] = useState<PnLFilter>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [search, setSearch] = useState('');

  // -- Sort state -----------------------------------------------------------
  const [sortKey, setSortKey] = useState<string>('timestamp');
  const [sortDir, setSortDir] = useState<SortDirection>('desc');

  // -- Pagination state -----------------------------------------------------
  const [page, setPage] = useState(1);

  // -- Build current price map from strategy_log ----------------------------
  const currentPrices = useMemo(() => {
    const prices = new Map<string, number>();
    for (const log of strategyLog) {
      if (log.current_price && log.pair) {
        const asset = extractBaseAsset(log.pair);
        if (!prices.has(asset)) {
          prices.set(asset, log.current_price);
        }
      }
    }
    return prices;
  }, [strategyLog]);

  // -- Derived: filtered & sorted trades with open/closed separation --------
  const { openTrades, closedTrades, filteredTrades } = useMemo(() => {
    let result = trades;

    // Strategy filter
    if (strategyFilter !== 'All') {
      result = result.filter((t) => t.strategy === strategyFilter);
    }

    // Exchange filter
    if (exchangeFilterLocal !== 'All') {
      result = result.filter((t) => t.exchange === exchangeFilterLocal);
    }

    // Position type filter
    if (positionTypeFilter !== 'All') {
      result = result.filter((t) => t.position_type === positionTypeFilter);
    }

    // P&L filter
    if (pnlFilter === 'profit') {
      result = result.filter((t) => t.pnl > 0);
    } else if (pnlFilter === 'loss') {
      result = result.filter((t) => t.pnl < 0);
    }

    // Date range
    if (dateFrom) {
      const from = new Date(dateFrom).getTime();
      result = result.filter((t) => new Date(t.timestamp).getTime() >= from);
    }
    if (dateTo) {
      const to = new Date(dateTo).getTime() + 86_399_999;
      result = result.filter((t) => new Date(t.timestamp).getTime() <= to);
    }

    // Search by pair name
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter((t) => t.pair.toLowerCase().includes(q));
    }

    // Split into open and closed/cancelled
    const open = result
      .filter((t) => t.status === 'open')
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    const closed = result
      .filter((t) => t.status !== 'open')
      .sort((a, b) => compareTrades(a, b, sortKey, sortDir));

    // Combined: open first, then closed
    const combined = [...open, ...closed];

    return { openTrades: open, closedTrades: closed, filteredTrades: combined };
  }, [trades, strategyFilter, exchangeFilterLocal, positionTypeFilter, pnlFilter, dateFrom, dateTo, search, sortKey, sortDir]);

  // -- Derived: pagination --------------------------------------------------
  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / TRADES_PER_PAGE));
  const safePage = Math.min(page, totalPages);
  const startIdx = (safePage - 1) * TRADES_PER_PAGE;
  const endIdx = Math.min(startIdx + TRADES_PER_PAGE, filteredTrades.length);
  const visibleTrades = filteredTrades.slice(startIdx, endIdx);

  // -- Handlers -------------------------------------------------------------
  const handleSort = useCallback(
    (key: string) => {
      if (key === 'exit_price' || key === 'id' || key === 'hold_time' || key === 'exit_reason' || key === 'fees' || key === 'gross_pnl' || key === 'setup_type' || key === 'sl_price' || key === 'trail_info' || key === 'peak_info') return; // Not sortable
      if (key === sortKey) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortKey(key);
        setSortDir('desc');
      }
      setPage(1);
    },
    [sortKey],
  );

  const handleStrategyFilter = useCallback((value: Strategy | 'All') => {
    setStrategyFilter(value);
    setPage(1);
  }, []);

  const handleExchangeFilter = useCallback((value: Exchange | 'All') => {
    setExchangeFilterLocal(value);
    setPage(1);
  }, []);

  const handlePositionTypeFilter = useCallback((value: PositionType | 'All') => {
    setPositionTypeFilter(value);
    setPage(1);
  }, []);

  const handlePnlFilter = useCallback((value: PnLFilter) => {
    setPnlFilter(value);
    setPage(1);
  }, []);

  const handleDateFrom = useCallback((value: string) => {
    setDateFrom(value);
    setPage(1);
  }, []);

  const handleDateTo = useCallback((value: string) => {
    setDateTo(value);
    setPage(1);
  }, []);

  const handleSearch = useCallback((value: string) => {
    setSearch(value);
    setPage(1);
  }, []);

  const exportCSV = useCallback(() => {
    const csv = tradesToCSV(filteredTrades as unknown as Array<Record<string, unknown>>);
    if (!csv) return;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [filteredTrades]);

  // -- Render helpers -------------------------------------------------------
  const filterBtnBase =
    'px-3 py-1.5 text-xs font-medium rounded-lg transition-colors';
  const filterBtnActive = 'bg-zinc-700 text-white';
  const filterBtnInactive = 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800';

  /** Get P&L display values for a trade (realized or unrealized) */
  function getDisplayPnL(trade: Trade): { pnl: number; pnlPct: number | null; isUnrealized: boolean } {
    if (trade.status === 'closed') {
      return {
        pnl: trade.pnl,
        pnlPct: trade.pnl_pct ?? null,
        isUnrealized: false,
      };
    }

    if (trade.status === 'open') {
      const asset = extractBaseAsset(trade.pair);
      // Priority: live API price (3s) → strategy_log price (~5min)
      const currentPrice = livePrices.prices[trade.pair] ?? currentPrices.get(asset) ?? null;
      const unrealized = calcUnrealizedPnL(trade, currentPrice);
      if (unrealized) {
        return {
          pnl: unrealized.pnl,
          pnlPct: unrealized.pnl_pct,
          isUnrealized: true,
        };
      }
    }

    return { pnl: trade.pnl, pnlPct: trade.pnl_pct ?? null, isUnrealized: false };
  }

  // -------------------------------------------------------------------------
  return (
    <div className="space-y-4">
      {/* ----------------------------------------------------------------- */}
      {/* Filters                                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        {/* Left side filters */}
        <div className="flex flex-wrap items-end gap-3 md:gap-4">
          {/* Strategy filter */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Strategy</span>
            <div className="flex gap-1 overflow-x-auto">
              {(['All', ...STRATEGIES] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => handleStrategyFilter(s)}
                  className={cn(
                    filterBtnBase,
                    strategyFilter === s ? filterBtnActive : filterBtnInactive,
                  )}
                >
                  {s === 'All' ? 'All' : getStrategyLabel(s)}
                </button>
              ))}
            </div>
          </div>

          {/* Exchange filter */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Exchange</span>
            <div className="flex gap-1">
              {EXCHANGES.map((ex) => (
                <button
                  key={ex.value}
                  onClick={() => handleExchangeFilter(ex.value)}
                  className={cn(
                    filterBtnBase,
                    exchangeFilterLocal === ex.value ? filterBtnActive : filterBtnInactive,
                  )}
                >
                  {ex.label}
                </button>
              ))}
            </div>
          </div>

          {/* Position Type filter */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Position</span>
            <div className="flex gap-1">
              {POSITION_TYPES.map((pt) => (
                <button
                  key={pt.value}
                  onClick={() => handlePositionTypeFilter(pt.value)}
                  className={cn(
                    filterBtnBase,
                    positionTypeFilter === pt.value ? filterBtnActive : filterBtnInactive,
                  )}
                >
                  {pt.label}
                </button>
              ))}
            </div>
          </div>

          {/* P&L filter */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">P&L</span>
            <div className="flex gap-1">
              {PNL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handlePnlFilter(opt.value)}
                  className={cn(
                    filterBtnBase,
                    pnlFilter === opt.value ? filterBtnActive : filterBtnInactive,
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Date range */}
          <div className="space-y-1.5 w-full sm:w-auto">
            <span className="text-xs font-medium text-zinc-400">Date Range</span>
            <div className="flex items-center gap-2">
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => handleDateFrom(e.target.value)}
                className="h-9 md:h-8 flex-1 sm:flex-none rounded-lg border border-zinc-700 bg-zinc-800 px-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
              />
              <span className="text-zinc-500">&ndash;</span>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => handleDateTo(e.target.value)}
                className="h-9 md:h-8 flex-1 sm:flex-none rounded-lg border border-zinc-700 bg-zinc-800 px-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
              />
            </div>
          </div>

          {/* Search */}
          <div className="space-y-1.5 w-full sm:w-auto">
            <span className="text-xs font-medium text-zinc-400">Search</span>
            <input
              type="text"
              value={search}
              onChange={(e) => handleSearch(e.target.value)}
              placeholder="Search pair..."
              className="h-9 md:h-8 w-full sm:w-40 rounded-lg border border-zinc-700 bg-zinc-800 px-3 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-zinc-500"
            />
          </div>
        </div>

        {/* Export CSV */}
        <button
          onClick={exportCSV}
          disabled={filteredTrades.length === 0}
          className="flex h-9 md:h-8 shrink-0 items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 16 16"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h2.879a1.5 1.5 0 0 1 1.06.44l1.122 1.12A1.5 1.5 0 0 0 9.62 4H12.5A1.5 1.5 0 0 1 14 5.5v1.382a1.5 1.5 0 0 1-.44 1.06l-.293.294a1 1 0 0 0-.293.707V12.5a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 12.5v-9Z" />
          </svg>
          <span className="hidden sm:inline">Export CSV</span>
        </button>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Summary bar                                                        */}
      {/* ----------------------------------------------------------------- */}
      <div className="flex items-center gap-4 text-xs text-zinc-400">
        <span>{openTrades.length} open</span>
        <span className="text-zinc-700">|</span>
        <span>{closedTrades.length} closed</span>
        <span className="text-zinc-700">|</span>
        <span>{filteredTrades.length} total</span>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Mobile card view                                                   */}
      {/* ----------------------------------------------------------------- */}
      <div className="md:hidden">
        {visibleTrades.length === 0 ? (
          <div className="rounded-xl border border-zinc-800 bg-card px-4 py-16 text-center text-sm text-zinc-500">
            No trades match your filters
          </div>
        ) : (
          <div className="space-y-2">
            {visibleTrades.map((trade, idx) => {
              const display = getDisplayPnL(trade);
              // Show section divider between open and closed
              const prevTrade = idx > 0 ? visibleTrades[idx - 1] : null;
              const showDivider = prevTrade?.status === 'open' && trade.status !== 'open';

              return (
                <div key={trade.id}>
                  {showDivider && (
                    <div className="flex items-center gap-2 py-2">
                      <div className="flex-1 border-t border-zinc-700" />
                      <span className="text-[10px] uppercase tracking-wider text-zinc-500">Closed Trades</span>
                      <div className="flex-1 border-t border-zinc-700" />
                    </div>
                  )}
                  <div className={cn(
                    'border rounded-lg p-3',
                    trade.status === 'open'
                      ? 'bg-zinc-900/60 border-zinc-700'
                      : 'bg-zinc-900/40 border-zinc-800/50',
                  )}>
                    {/* Top row: Pair + Type + P&L */}
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-zinc-500">
                          #{typeof trade.id === 'string' && trade.id.length > 6
                            ? trade.id.slice(-6)
                            : trade.id}
                        </span>
                        <span className="text-sm font-semibold text-white">{displayPair(trade.pair)}</span>
                        <span className={cn('text-[10px] font-medium', getPositionTypeColor(trade.position_type))}>
                          {getPositionTypeLabel(trade.position_type)}
                        </span>
                        <span
                          className="inline-block h-2 w-2 rounded-full"
                          style={{ backgroundColor: getExchangeColor(trade.exchange) }}
                        />
                      </div>
                      <div className="text-right">
                        <span
                          className={cn(
                            'text-sm font-mono font-semibold',
                            getPnLColor(display.pnl),
                          )}
                        >
                          {formatPnL(display.pnl)}
                        </span>
                        {display.isUnrealized && (
                          <span className="text-[9px] text-zinc-500 ml-1">live</span>
                        )}
                        {trade.status === 'closed' && trade.gross_pnl != null && (
                          <div className="text-[10px] text-zinc-500 font-mono">
                            gross {formatPnL(trade.gross_pnl)} · fees -${((trade.entry_fee ?? 0) + (trade.exit_fee ?? 0)).toFixed(4)}
                          </div>
                        )}
                      </div>
                    </div>
                    {/* Prices row */}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs mb-1">
                      <span className="text-zinc-400">
                        Entry: <span className="font-mono text-zinc-300">{formatPrice(trade.price)}</span>
                      </span>
                      {trade.exit_price != null && (
                        <span className="text-zinc-400">
                          Exit: <span className="font-mono text-zinc-300">{formatPrice(trade.exit_price)}</span>
                        </span>
                      )}
                      {trade.exchange === 'delta' && (
                        <span className="text-zinc-500 font-mono">{trade.amount} contracts</span>
                      )}
                    </div>
                    {/* Details row */}
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
                      <span>{formatDate(trade.timestamp)}</span>
                      <Badge variant={getStrategyBadgeVariant(trade.strategy)}>
                        {getStrategyLabel(trade.strategy)}
                      </Badge>
                      {trade.setup_type && (
                        <span className={cn(
                          'inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold',
                          SETUP_COLORS[trade.setup_type]?.bg ?? 'bg-zinc-500/10',
                          SETUP_COLORS[trade.setup_type]?.text ?? 'text-zinc-400',
                        )}>
                          {getSetupLabel(trade.setup_type)}
                        </span>
                      )}
                      {trade.leverage > 1 && (
                        <span className="text-amber-400 font-mono">{formatLeverage(trade.leverage)}</span>
                      )}
                      {display.pnlPct != null && (
                        <span className={cn('font-mono', getPnLColor(display.pnlPct))}>
                          {formatPercentage(display.pnlPct)}
                          {display.isUnrealized ? ' (unr)' : ''}
                        </span>
                      )}
                      <HoldTimeCell trade={trade} now={now} />
                      {trade.status === 'open' && trade.position_state === 'trailing' && (
                        <span className="text-emerald-400 text-[10px]">
                          &#x1F7E2; TRAIL {trade.peak_pnl != null ? `+${trade.peak_pnl.toFixed(2)}%` : ''}
                        </span>
                      )}
                      {trade.status === 'open' && trade.position_state === 'holding' && (
                        <span className="text-zinc-500 text-[10px]">
                          &#x23F3; {trade.current_pnl != null ? `${trade.current_pnl >= 0 ? '+' : ''}${trade.current_pnl.toFixed(2)}%` : 'holding'}
                        </span>
                      )}
                      {trade.status === 'open' && trade.stop_loss != null && (
                        <span className="text-red-400 text-[10px] font-mono">
                          SL {formatPrice(trade.stop_loss)}
                        </span>
                      )}
                      {trade.status !== 'open' && (() => {
                        const reason = getExitReason(trade);
                        return reason ? (
                          <span className={cn('font-semibold', getExitReasonColor(reason))}>
                            {reason}
                          </span>
                        ) : null;
                      })()}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Mobile pagination */}
        {filteredTrades.length > 0 && (
          <div className="flex items-center justify-between mt-3">
            <span className="text-xs text-zinc-400">
              {startIdx + 1}&ndash;{endIdx} of {filteredTrades.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage <= 1}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-300 disabled:opacity-40"
              >
                Prev
              </button>
              <span className="text-xs text-zinc-400">
                {safePage}/{totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage >= totalPages}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-300 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Desktop table                                                      */}
      {/* ----------------------------------------------------------------- */}
      <div className="hidden md:block bg-card overflow-hidden rounded-xl border border-zinc-800">
        {/* Top scrollbar — mirrors the table scroll */}
        <div
          ref={topScrollRef}
          className="overflow-x-auto scrollbar-visible"
          onScroll={() => {
            if (tableScrollRef.current && topScrollRef.current) {
              tableScrollRef.current.scrollLeft = topScrollRef.current.scrollLeft;
            }
          }}
        >
          <div style={{ width: `${tableScrollWidth}px`, height: '1px' }} />
        </div>
        <div
          ref={tableScrollRef}
          className="overflow-x-auto scrollbar-visible"
          onScroll={() => {
            if (topScrollRef.current && tableScrollRef.current) {
              topScrollRef.current.scrollLeft = tableScrollRef.current.scrollLeft;
            }
          }}
        >
          <table className="w-full min-w-[2100px] text-sm">
            {/* Header */}
            <thead>
              <tr className="bg-zinc-900/50">
                {COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => handleSort(col.key)}
                    className={cn(
                      'cursor-pointer select-none whitespace-nowrap px-4 py-3 text-xs font-medium uppercase tracking-wider text-zinc-400 transition-colors hover:text-zinc-200',
                      col.align === 'right' ? 'text-right' : 'text-left',
                      STICKY_COLS[col.key] && `sticky ${STICKY_COLS[col.key]} z-20 bg-zinc-900`,
                      col.key === LAST_STICKY_COL && 'border-r border-zinc-700',
                    )}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {sortKey === col.key && (
                        <span className="text-zinc-300">
                          {sortDir === 'asc' ? '\u25B2' : '\u25BC'}
                        </span>
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>

            {/* Body */}
            <tbody>
              {visibleTrades.length === 0 ? (
                <tr>
                  <td
                    colSpan={COLUMNS.length}
                    className="px-4 py-16 text-center text-sm text-zinc-500"
                  >
                    No trades match your filters
                  </td>
                </tr>
              ) : (
                visibleTrades.map((trade, idx) => {
                  const display = getDisplayPnL(trade);
                  const prevTrade = idx > 0 ? visibleTrades[idx - 1] : null;
                  const showDivider = prevTrade?.status === 'open' && trade.status !== 'open';

                  return (
                    <>
                      {showDivider && (
                        <tr key={`divider-${trade.id}`}>
                          <td colSpan={COLUMNS.length} className="px-4 py-2 bg-zinc-900/80">
                            <div className="flex items-center gap-2">
                              <div className="flex-1 border-t border-zinc-700" />
                              <span className="text-[10px] uppercase tracking-wider text-zinc-500">Closed Trades</span>
                              <div className="flex-1 border-t border-zinc-700" />
                            </div>
                          </td>
                        </tr>
                      )}
                      <tr
                        key={trade.id}
                        className={cn(
                          'border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/30',
                          trade.status === 'open' && 'bg-zinc-900/30',
                        )}
                      >
                        {/* ── Frozen sticky columns ── */}

                        {/* Pair — STICKY */}
                        <td className={cn(
                          'sticky left-0 z-10 whitespace-nowrap px-4 py-3 font-medium text-zinc-100',
                          trade.status === 'open' ? 'bg-zinc-900' : 'bg-[#0d1117]',
                        )}>
                          {displayPair(trade.pair)}
                        </td>

                        {/* Type — STICKY */}
                        <td className={cn(
                          'sticky left-[100px] z-10 whitespace-nowrap px-4 py-3',
                          trade.status === 'open' ? 'bg-zinc-900' : 'bg-[#0d1117]',
                        )}>
                          <span className={cn('text-xs font-medium', getPositionTypeColor(trade.position_type))}>
                            {getPositionTypeLabel(trade.position_type)}
                          </span>
                        </td>

                        {/* Leverage — STICKY */}
                        <td className={cn(
                          'sticky left-[180px] z-10 whitespace-nowrap px-4 py-3 text-right',
                          trade.status === 'open' ? 'bg-zinc-900' : 'bg-[#0d1117]',
                        )}>
                          {trade.leverage > 1 ? (
                            <span className="text-xs font-medium text-amber-400">
                              {formatLeverage(trade.leverage)}
                            </span>
                          ) : (
                            <span className="text-xs text-zinc-500">&mdash;</span>
                          )}
                        </td>

                        {/* Entry Price — STICKY + right border */}
                        <td className={cn(
                          'sticky left-[240px] z-10 border-r border-zinc-700 whitespace-nowrap px-4 py-3 text-right font-mono text-zinc-300',
                          trade.status === 'open' ? 'bg-zinc-900' : 'bg-[#0d1117]',
                        )}>
                          {formatPrice(trade.price)}
                        </td>

                        {/* ── Scrollable columns ── */}

                        {/* ID */}
                        <td className="whitespace-nowrap px-4 py-3 text-xs font-mono text-zinc-500">
                          #{typeof trade.id === 'string' && trade.id.length > 6
                            ? trade.id.slice(-6)
                            : trade.id}
                        </td>

                        {/* Date */}
                        <td className="whitespace-nowrap px-4 py-3 text-zinc-300">
                          {formatDate(trade.timestamp)}
                        </td>

                        {/* Exchange */}
                        <td className="whitespace-nowrap px-4 py-3">
                          <span className="inline-flex items-center gap-1.5">
                            <span
                              className="inline-block h-2 w-2 rounded-full"
                              style={{ backgroundColor: getExchangeColor(trade.exchange) }}
                            />
                            <span className="text-zinc-300 text-xs">
                              {getExchangeLabel(trade.exchange)}
                            </span>
                          </span>
                        </td>

                        {/* Exit Price */}
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-zinc-300">
                          {trade.exit_price != null ? (
                            formatPrice(trade.exit_price)
                          ) : trade.status === 'open' ? (
                            <span className="text-zinc-500 text-xs italic">open</span>
                          ) : (
                            <span className="text-zinc-600">&mdash;</span>
                          )}
                        </td>

                        {/* Contracts / Amount */}
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-zinc-300">
                          {trade.exchange === 'delta' ? (
                            <span title={`${trade.amount} contracts`}>
                              {trade.amount.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                              <span className="text-zinc-500 text-[10px] ml-0.5">ct</span>
                            </span>
                          ) : (
                            trade.amount.toLocaleString('en-US', {
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 6,
                            })
                          )}
                        </td>

                        {/* Strategy */}
                        <td className="whitespace-nowrap px-4 py-3">
                          <Badge variant={getStrategyBadgeVariant(trade.strategy)}>
                            {getStrategyLabel(trade.strategy)}
                          </Badge>
                        </td>

                        {/* Setup Type */}
                        <td className="whitespace-nowrap px-4 py-3">
                          {trade.setup_type ? (
                            <span className={cn(
                              'inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold',
                              SETUP_COLORS[trade.setup_type]?.bg ?? 'bg-zinc-500/10',
                              SETUP_COLORS[trade.setup_type]?.text ?? 'text-zinc-400',
                            )}>
                              {getSetupLabel(trade.setup_type)}
                            </span>
                          ) : (
                            <span className="text-zinc-600 text-xs">—</span>
                          )}
                        </td>

                        {/* Gross P&L */}
                        <td
                          className={cn(
                            'whitespace-nowrap px-4 py-3 text-right font-mono text-xs',
                            trade.status === 'open'
                              ? getPnLColor(display.pnl)
                              : getPnLColor(trade.gross_pnl ?? display.pnl),
                          )}
                        >
                          {trade.status === 'open' ? (
                            <>
                              {formatPnL(display.pnl)}
                              {display.isUnrealized && (
                                <span className="text-[9px] text-zinc-500 ml-0.5 font-normal">live</span>
                              )}
                            </>
                          ) : trade.gross_pnl != null ? (
                            formatPnL(trade.gross_pnl)
                          ) : (
                            <span className="text-zinc-600">&mdash;</span>
                          )}
                        </td>

                        {/* Fees (entry + exit) */}
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-zinc-500">
                          {trade.status === 'closed' && (trade.entry_fee != null || trade.exit_fee != null) ? (
                            <span title={`Entry: $${(trade.entry_fee ?? 0).toFixed(4)} + Exit: $${(trade.exit_fee ?? 0).toFixed(4)}`}>
                              -${((trade.entry_fee ?? 0) + (trade.exit_fee ?? 0)).toFixed(4)}
                            </span>
                          ) : (
                            <span className="text-zinc-600">&mdash;</span>
                          )}
                        </td>

                        {/* Net P&L */}
                        <td
                          className={cn(
                            'whitespace-nowrap px-4 py-3 text-right font-mono font-medium',
                            getPnLColor(display.pnl),
                          )}
                        >
                          {formatPnL(display.pnl)}
                          {display.isUnrealized && (
                            <span className="text-[9px] text-zinc-500 ml-0.5 font-normal">live</span>
                          )}
                        </td>

                        {/* P&L % (return on collateral) */}
                        <td
                          className={cn(
                            'whitespace-nowrap px-4 py-3 text-right font-mono text-xs',
                            getPnLColor(display.pnlPct ?? 0),
                          )}
                        >
                          {display.pnlPct != null
                            ? (
                              <>
                                {formatPercentage(display.pnlPct)}
                                {display.isUnrealized && (
                                  <span className="text-[9px] text-zinc-500 ml-0.5">unr</span>
                                )}
                              </>
                            )
                            : trade.status === 'closed' ? '+0.00%' : '—'}
                        </td>

                        {/* Hold Time */}
                        <td className="whitespace-nowrap px-4 py-3 text-right">
                          <HoldTimeCell trade={trade} now={now} />
                        </td>

                        {/* SL Price */}
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs">
                          {trade.stop_loss != null ? (
                            <span className={trade.status === 'open' ? 'text-red-400' : 'text-zinc-500'}>{formatPrice(trade.stop_loss)}</span>
                          ) : (
                            <span className="text-zinc-600">&mdash;</span>
                          )}
                        </td>

                        {/* Trail Info */}
                        <td className="px-4 py-3 text-xs">
                          {trade.status === 'open' ? (
                            trade.position_state === 'trailing' ? (
                              <div className="flex items-center gap-1.5 min-w-[120px]">
                                <div className="flex-1 max-w-[100px]">
                                  <div className="h-1.5 rounded-full bg-emerald-400/30 overflow-hidden">
                                    <div className="h-full rounded-full bg-emerald-400 animate-pulse" style={{ width: '100%' }} />
                                  </div>
                                </div>
                                <span className="text-[10px] font-mono text-emerald-400 font-semibold whitespace-nowrap">
                                  TRAILING
                                </span>
                              </div>
                            ) : trade.position_state === 'holding' || trade.status === 'open' ? (() => {
                              const TRAIL_ACT = 0.30;
                              // Compute CURRENT live price P&L (moves up AND down with price)
                              let currentPnl = trade.current_pnl ?? 0;
                              const livePrice = livePrices.prices[trade.pair] ?? null;
                              if (livePrice && trade.price > 0) {
                                currentPnl = trade.position_type === 'short'
                                  ? ((trade.price - livePrice) / trade.price) * 100
                                  : ((livePrice - trade.price) / trade.price) * 100;
                              }
                              // Bar shows CURRENT position relative to 0.30% — goes up and down
                              const displayPnl = Math.max(currentPnl, 0);
                              const progress = Math.min((displayPnl / TRAIL_ACT) * 100, 100);
                              const barColor = progress >= 66 ? 'bg-emerald-400' : progress >= 33 ? 'bg-amber-400' : 'bg-red-400';
                              const txtColor = progress >= 66 ? 'text-emerald-400' : progress >= 33 ? 'text-amber-400' : 'text-red-400';
                              return (
                                <div className="flex items-center gap-1.5 min-w-[120px]">
                                  <div className="flex-1 max-w-[100px]">
                                    <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                                      <div
                                        className={cn('h-full rounded-full transition-all duration-500', barColor)}
                                        style={{ width: `${progress}%` }}
                                      />
                                    </div>
                                  </div>
                                  <span className={cn('text-[10px] font-mono whitespace-nowrap', txtColor)}>
                                    {displayPnl.toFixed(2)}/{TRAIL_ACT.toFixed(2)}%
                                  </span>
                                </div>
                              );
                            })() : (
                              <span className="text-zinc-600">&mdash;</span>
                            )
                          ) : (() => {
                            const exitR = getExitReason(trade);
                            if (exitR === 'TRAIL') return <span className="text-emerald-400">Trailed</span>;
                            if (exitR === 'PROFIT_LOCK') return <span className="text-emerald-400">Locked</span>;
                            if (trade.trail_stop_price != null) return <span className="text-zinc-500">@ {formatPrice(trade.trail_stop_price)}</span>;
                            return <span className="text-zinc-600">&mdash;</span>;
                          })()}
                        </td>

                        {/* Peak P&L */}
                        <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-xs">
                          {trade.peak_pnl != null ? (
                            <span className={cn(
                              trade.peak_pnl >= 0.3 ? 'text-emerald-400' :
                              trade.peak_pnl >= 0.1 ? 'text-yellow-400' :
                              trade.peak_pnl >= 0 ? 'text-zinc-400' : 'text-red-400'
                            )}>
                              {trade.peak_pnl >= 0 ? '+' : ''}{trade.peak_pnl.toFixed(2)}%
                            </span>
                          ) : (
                            <span className="text-zinc-600">&mdash;</span>
                          )}
                        </td>

                        {/* Exit Reason */}
                        <td className="whitespace-nowrap px-4 py-3">
                          {trade.status === 'open' ? (
                            <span className="text-zinc-600">&mdash;</span>
                          ) : (() => {
                            const reason = getExitReason(trade);
                            return reason ? (
                              <span className={cn('text-xs font-semibold', getExitReasonColor(reason))}>
                                {reason}
                              </span>
                            ) : (
                              <span className="text-zinc-600">&mdash;</span>
                            );
                          })()}
                        </td>

                        {/* Status */}
                        <td className="whitespace-nowrap px-4 py-3">
                          <Badge variant={getStatusBadgeVariant(trade.status)}>
                            {trade.status.charAt(0).toUpperCase() + trade.status.slice(1)}
                          </Badge>
                        </td>
                      </tr>
                    </>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {filteredTrades.length > 0 && (
          <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3">
            <span className="text-xs text-zinc-400">
              Showing {startIdx + 1}&ndash;{endIdx} of {filteredTrades.length} trades
            </span>

            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage <= 1}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                Previous
              </button>

              <span className="min-w-[4rem] text-center text-xs text-zinc-400">
                Page {safePage} of {totalPages}
              </span>

              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage >= totalPages}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
