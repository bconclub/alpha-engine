'use client';

import { useState, useMemo, useCallback } from 'react';
import type { Trade, Strategy, Exchange, PositionType } from '@/lib/types';
import {
  formatCurrency,
  formatPnL,
  formatDate,
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey = keyof Pick<
  Trade,
  'timestamp' | 'pair' | 'side' | 'price' | 'amount' | 'strategy' | 'pnl' | 'status' | 'exchange' | 'position_type' | 'leverage'
>;

type SortDirection = 'asc' | 'desc';

type PnLFilter = 'all' | 'profit' | 'loss';

interface TradeTableProps {
  trades: Trade[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STRATEGIES: Strategy[] = ['momentum', 'futures_momentum', 'grid', 'scalp'];
const EXCHANGES: { label: string; value: Exchange | 'All' }[] = [
  { label: 'All', value: 'All' },
  { label: 'Binance', value: 'binance' },
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

const COLUMNS: { key: SortKey; label: string; align?: 'right' }[] = [
  { key: 'timestamp', label: 'Date' },
  { key: 'pair', label: 'Pair' },
  { key: 'exchange', label: 'Exchange' },
  { key: 'side', label: 'Side' },
  { key: 'position_type', label: 'Type' },
  { key: 'leverage', label: 'Leverage', align: 'right' },
  { key: 'price', label: 'Price', align: 'right' },
  { key: 'amount', label: 'Amount', align: 'right' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'pnl', label: 'P&L', align: 'right' },
  { key: 'status', label: 'Status' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// getStrategyBadgeVariant is now imported from @/lib/utils

function getStatusBadgeVariant(status: Trade['status']) {
  const map: Record<Trade['status'], 'success' | 'danger' | 'default'> = {
    open: 'success',
    closed: 'default',
    cancelled: 'danger',
  };
  return map[status];
}

function compareTrades(a: Trade, b: Trade, key: SortKey, dir: SortDirection): number {
  let aVal: string | number = a[key];
  let bVal: string | number = b[key];

  // For timestamp, compare as dates
  if (key === 'timestamp') {
    aVal = new Date(aVal as string).getTime();
    bVal = new Date(bVal as string).getTime();
  }

  // For strings, compare case-insensitively
  if (typeof aVal === 'string' && typeof bVal === 'string') {
    aVal = aVal.toLowerCase();
    bVal = bVal.toLowerCase();
  }

  if (aVal < bVal) return dir === 'asc' ? -1 : 1;
  if (aVal > bVal) return dir === 'asc' ? 1 : -1;
  return 0;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TradeTable({ trades }: TradeTableProps) {
  // -- Filter state ---------------------------------------------------------
  const [strategyFilter, setStrategyFilter] = useState<Strategy | 'All'>('All');
  const [exchangeFilterLocal, setExchangeFilterLocal] = useState<Exchange | 'All'>('All');
  const [positionTypeFilter, setPositionTypeFilter] = useState<PositionType | 'All'>('All');
  const [pnlFilter, setPnlFilter] = useState<PnLFilter>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [search, setSearch] = useState('');

  // -- Sort state -----------------------------------------------------------
  const [sortKey, setSortKey] = useState<SortKey>('timestamp');
  const [sortDir, setSortDir] = useState<SortDirection>('desc');

  // -- Pagination state -----------------------------------------------------
  const [page, setPage] = useState(1);

  // -- Derived: filtered & sorted trades ------------------------------------
  const filteredTrades = useMemo(() => {
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
      // Include the full "to" day by setting to end-of-day
      const to = new Date(dateTo).getTime() + 86_399_999;
      result = result.filter((t) => new Date(t.timestamp).getTime() <= to);
    }

    // Search by pair name
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter((t) => t.pair.toLowerCase().includes(q));
    }

    // Sort
    result = [...result].sort((a, b) => compareTrades(a, b, sortKey, sortDir));

    return result;
  }, [trades, strategyFilter, exchangeFilterLocal, positionTypeFilter, pnlFilter, dateFrom, dateTo, search, sortKey, sortDir]);

  // -- Derived: pagination --------------------------------------------------
  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / TRADES_PER_PAGE));
  const safePage = Math.min(page, totalPages);
  const startIdx = (safePage - 1) * TRADES_PER_PAGE;
  const endIdx = Math.min(startIdx + TRADES_PER_PAGE, filteredTrades.length);
  const visibleTrades = filteredTrades.slice(startIdx, endIdx);

  // Reset to page 1 whenever filters change — handled via key resets below
  // We use safePage for display so the user never sees an out-of-range page.

  // -- Handlers -------------------------------------------------------------
  const handleSort = useCallback(
    (key: SortKey) => {
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

  // -------------------------------------------------------------------------
  return (
    <div className="space-y-4">
      {/* ----------------------------------------------------------------- */}
      {/* Filters                                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        {/* Left side filters */}
        <div className="flex flex-wrap items-end gap-4">
          {/* Strategy filter */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Strategy</span>
            <div className="flex gap-1">
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
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Date Range</span>
            <div className="flex items-center gap-2">
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => handleDateFrom(e.target.value)}
                className="h-8 rounded-lg border border-zinc-700 bg-zinc-800 px-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
              />
              <span className="text-zinc-500">&ndash;</span>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => handleDateTo(e.target.value)}
                className="h-8 rounded-lg border border-zinc-700 bg-zinc-800 px-2 text-xs text-zinc-200 outline-none focus:border-zinc-500"
              />
            </div>
          </div>

          {/* Search */}
          <div className="space-y-1.5">
            <span className="text-xs font-medium text-zinc-400">Search</span>
            <input
              type="text"
              value={search}
              onChange={(e) => handleSearch(e.target.value)}
              placeholder="Search pair..."
              className="h-8 w-40 rounded-lg border border-zinc-700 bg-zinc-800 px-3 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-zinc-500"
            />
          </div>
        </div>

        {/* Export CSV */}
        <button
          onClick={exportCSV}
          disabled={filteredTrades.length === 0}
          className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 16 16"
            fill="currentColor"
            className="h-3.5 w-3.5"
          >
            <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h2.879a1.5 1.5 0 0 1 1.06.44l1.122 1.12A1.5 1.5 0 0 0 9.62 4H12.5A1.5 1.5 0 0 1 14 5.5v1.382a1.5 1.5 0 0 1-.44 1.06l-.293.294a1 1 0 0 0-.293.707V12.5a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 12.5v-9Z" />
          </svg>
          Export CSV
        </button>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Table                                                             */}
      {/* ----------------------------------------------------------------- */}
      <div className="bg-card overflow-hidden rounded-xl border border-zinc-800">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1200px] text-sm">
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
                visibleTrades.map((trade) => (
                  <tr
                    key={trade.id}
                    className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/30"
                  >
                    {/* Date */}
                    <td className="whitespace-nowrap px-4 py-3 text-zinc-300">
                      {formatDate(trade.timestamp)}
                    </td>

                    {/* Pair */}
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-zinc-100">
                      {trade.pair}
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

                    {/* Side */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <Badge variant={trade.side === 'buy' ? 'success' : 'danger'}>
                        {trade.side.toUpperCase()}
                      </Badge>
                    </td>

                    {/* Type (position_type) */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <span className={cn('text-xs font-medium', getPositionTypeColor(trade.position_type))}>
                        {getPositionTypeLabel(trade.position_type)}
                      </span>
                    </td>

                    {/* Leverage */}
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      {trade.leverage > 1 ? (
                        <span className="text-xs font-medium text-amber-400">
                          {formatLeverage(trade.leverage)}
                        </span>
                      ) : (
                        <span className="text-xs text-zinc-500">&mdash;</span>
                      )}
                    </td>

                    {/* Price */}
                    <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-zinc-300">
                      {formatCurrency(trade.price)}
                    </td>

                    {/* Amount */}
                    <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-zinc-300">
                      {trade.amount.toLocaleString('en-US', {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 6,
                      })}
                    </td>

                    {/* Strategy */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <Badge variant={getStrategyBadgeVariant(trade.strategy)}>
                        {getStrategyLabel(trade.strategy)}
                      </Badge>
                    </td>

                    {/* P&L */}
                    <td
                      className={cn(
                        'whitespace-nowrap px-4 py-3 text-right font-mono font-medium',
                        getPnLColor(trade.pnl),
                      )}
                    >
                      {formatPnL(trade.pnl)}
                    </td>

                    {/* Status */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <Badge variant={getStatusBadgeVariant(trade.status)}>
                        {trade.status.charAt(0).toUpperCase() + trade.status.slice(1)}
                      </Badge>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* --------------------------------------------------------------- */}
        {/* Pagination                                                       */}
        {/* --------------------------------------------------------------- */}
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
