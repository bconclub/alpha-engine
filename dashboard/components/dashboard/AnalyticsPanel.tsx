'use client';

import { useMemo, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
  BarChart,
  Bar,
  Cell,
  PieChart,
  Pie,
  AreaChart,
  Area,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import {
  formatCurrency,
  formatPnL,
  formatPercentage,
  formatShortDate,
  getExchangeLabel,
  getExchangeColor,
  cn,
} from '@/lib/utils';
// DailyPnL type is inferred from useSupabase()

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TimeRange = '7d' | '30d' | '90d' | 'all';

interface CumulativePnLPoint {
  timestamp: string;
  cumulativePnL: number;
}

interface DailyBarPoint {
  trade_date: string;
  daily_pnl: number;
}

interface PairPnLPoint {
  pair: string;
  pnl: number;
  trades: number;
}

interface ExchangePieSlice {
  name: string;
  value: number;
  pnl: number;
  color: string;
}

interface DrawdownPoint {
  timestamp: string;
  drawdown: number;
}

interface WinRatePoint {
  tradeIndex: number;
  timestamp: string;
  winRate: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const RANGE_MS: Record<TimeRange, number> = {
  '7d': 7 * 86_400_000,
  '30d': 30 * 86_400_000,
  '90d': 90 * 86_400_000,
  all: 0,
};

function cutoffDate(range: TimeRange): number {
  if (range === 'all') return 0;
  return Date.now() - RANGE_MS[range];
}

// ---------------------------------------------------------------------------
// Tooltip components
// ---------------------------------------------------------------------------

interface GenericPayload<T> {
  dataKey: string;
  value: number;
  color: string;
  payload: T;
}

function CumulativeTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<CumulativePnLPoint>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-400 mb-1">{formatShortDate(d.timestamp)}</p>
      <p className={d.cumulativePnL >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}>
        Cumulative: {formatPnL(d.cumulativePnL)}
      </p>
    </div>
  );
}

function DailyTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<DailyBarPoint>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-400 mb-1">{formatShortDate(d.trade_date)}</p>
      <p className={d.daily_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}>
        Daily P&L: {formatPnL(d.daily_pnl)}
      </p>
    </div>
  );
}

function PairTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<PairPnLPoint>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-300 font-medium mb-1">{d.pair}</p>
      <p className={d.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}>
        P&L: {formatPnL(d.pnl)}
      </p>
      <p className="text-zinc-400">{d.trades} trades</p>
    </div>
  );
}

function DrawdownTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<DrawdownPoint>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-400 mb-1">{formatShortDate(d.timestamp)}</p>
      <p className="text-[#ff1744]">Drawdown: {formatPercentage(d.drawdown)}</p>
    </div>
  );
}

function WinRateTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<WinRatePoint>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-400 mb-1">Trade #{d.tradeIndex}</p>
      <p className="text-[#2196f3]">Win Rate: {d.winRate.toFixed(1)}%</p>
    </div>
  );
}

function ExchangeTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: GenericPayload<ExchangePieSlice>[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-300 font-medium mb-1">{d.name}</p>
      <p className={d.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}>
        P&L: {formatPnL(d.pnl)}
      </p>
      <p className="text-zinc-400">{d.value} trades</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-3">
      <p className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={cn('text-sm font-mono font-semibold', valueClass ?? 'text-zinc-300')}>
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state placeholder
// ---------------------------------------------------------------------------

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="h-full flex items-center justify-center">
      <p className="text-xs text-zinc-500">{message}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function AnalyticsPanel() {
  const { trades, dailyPnL, pnlByExchange } = useSupabase();
  const [timeRange, setTimeRange] = useState<TimeRange>('all');

  // ---- Filter trades by time range ----
  const rangeFilteredTrades = useMemo(() => {
    const cutoff = cutoffDate(timeRange);
    if (cutoff === 0) return trades;
    return trades.filter((t) => new Date(t.timestamp).getTime() >= cutoff);
  }, [trades, timeRange]);

  // ---- Filter dailyPnL by time range ----
  const rangeFilteredDaily = useMemo(() => {
    const cutoff = cutoffDate(timeRange);
    if (cutoff === 0) return dailyPnL;
    return dailyPnL.filter((d) => new Date(d.trade_date).getTime() >= cutoff);
  }, [dailyPnL, timeRange]);

  // ---- Summary stats ----
  const closedTrades = useMemo(
    () => rangeFilteredTrades.filter((t) => t.status === 'closed'),
    [rangeFilteredTrades],
  );

  const totalPnL = useMemo(
    () => closedTrades.reduce((sum, t) => sum + t.pnl, 0),
    [closedTrades],
  );

  const wins = useMemo(() => closedTrades.filter((t) => t.pnl > 0).length, [closedTrades]);
  const winRate = closedTrades.length > 0 ? (wins / closedTrades.length) * 100 : 0;
  const avgPnL = closedTrades.length > 0 ? totalPnL / closedTrades.length : 0;

  // ---- Cumulative P&L over time ----
  const cumulativePnLData = useMemo<CumulativePnLPoint[]>(() => {
    const sorted = [...closedTrades].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );
    let running = 0;
    return sorted.map((t) => {
      running += t.pnl;
      return { timestamp: t.timestamp, cumulativePnL: running };
    });
  }, [closedTrades]);

  // ---- Daily P&L bar data ----
  const dailyBarData = useMemo<DailyBarPoint[]>(
    () =>
      rangeFilteredDaily.map((d) => ({
        trade_date: d.trade_date,
        daily_pnl: d.daily_pnl,
      })),
    [rangeFilteredDaily],
  );

  // ---- Per-pair breakdown ----
  const pairBreakdown = useMemo<PairPnLPoint[]>(() => {
    const byPair = new Map<string, { pnl: number; trades: number }>();
    for (const t of closedTrades) {
      const cur = byPair.get(t.pair) ?? { pnl: 0, trades: 0 };
      cur.pnl += t.pnl;
      cur.trades += 1;
      byPair.set(t.pair, cur);
    }
    return Array.from(byPair.entries())
      .map(([pair, stats]) => ({ pair, ...stats }))
      .sort((a, b) => b.pnl - a.pnl);
  }, [closedTrades]);

  // ---- Exchange pie data ----
  const exchangePieData = useMemo<ExchangePieSlice[]>(
    () =>
      pnlByExchange.map((ex) => ({
        name: getExchangeLabel(ex.exchange),
        value: ex.total_trades,
        pnl: ex.total_pnl,
        color: getExchangeColor(ex.exchange),
      })),
    [pnlByExchange],
  );

  // ---- Drawdown chart ----
  const drawdownData = useMemo<DrawdownPoint[]>(() => {
    if (cumulativePnLData.length === 0) return [];
    let peak = -Infinity;
    return cumulativePnLData.map((pt) => {
      if (pt.cumulativePnL > peak) peak = pt.cumulativePnL;
      const dd = peak > 0 ? ((pt.cumulativePnL - peak) / peak) * 100 : 0;
      return { timestamp: pt.timestamp, drawdown: Math.min(dd, 0) };
    });
  }, [cumulativePnLData]);

  // ---- Rolling 10-trade win rate ----
  const winRateOverTime = useMemo<WinRatePoint[]>(() => {
    const sorted = [...closedTrades].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );
    const windowSize = 10;
    if (sorted.length < windowSize) return [];
    const points: WinRatePoint[] = [];
    for (let i = windowSize - 1; i < sorted.length; i++) {
      const window = sorted.slice(i - windowSize + 1, i + 1);
      const windowWins = window.filter((t) => t.pnl > 0).length;
      points.push({
        tradeIndex: i + 1,
        timestamp: sorted[i].timestamp,
        winRate: (windowWins / windowSize) * 100,
      });
    }
    return points;
  }, [closedTrades]);

  // ---- Render ----

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
      {/* Header */}
      <div className="p-5 border-b border-zinc-800/60">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Analytics
        </h3>
      </div>

      <div className="px-5 pb-5 pt-4 space-y-6">
        {/* ----------------------------------------------------------------
            1. Time Range Selector
        ---------------------------------------------------------------- */}
        <div className="flex gap-1">
          {(['7d', '30d', '90d', 'all'] as TimeRange[]).map((range) => (
            <button
              key={range}
              onClick={() => setTimeRange(range)}
              className={cn(
                'px-3 py-1 rounded text-xs font-medium transition-colors',
                timeRange === range
                  ? 'bg-zinc-700 text-white'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50',
              )}
            >
              {range === '7d'
                ? '7 Days'
                : range === '30d'
                  ? '30 Days'
                  : range === '90d'
                    ? '90 Days'
                    : 'All Time'}
            </button>
          ))}
        </div>

        {/* ----------------------------------------------------------------
            2. Summary Stats Row
        ---------------------------------------------------------------- */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            label="Total P&L"
            value={formatPnL(totalPnL)}
            valueClass={totalPnL >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}
          />
          <StatCard
            label="Win Rate"
            value={formatPercentage(winRate)}
            valueClass={winRate >= 50 ? 'text-[#00c853]' : 'text-[#ff1744]'}
          />
          <StatCard label="Total Trades" value={String(closedTrades.length)} />
          <StatCard
            label="Avg P&L / Trade"
            value={formatPnL(avgPnL)}
            valueClass={avgPnL >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}
          />
        </div>

        {/* ----------------------------------------------------------------
            Charts grid: 2x2 on large screens
        ---------------------------------------------------------------- */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* --------------------------------------------------------------
              3. Cumulative P&L Line Chart
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              Cumulative P&L
            </h4>
            <div className="h-56">
              {cumulativePnLData.length === 0 ? (
                <EmptyChart message="No closed trades yet" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={cumulativePnLData}
                    margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
                  >
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(v: string) => formatShortDate(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                    />
                    <YAxis
                      tickFormatter={(v: number) => formatCurrency(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                      width={70}
                    />
                    <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
                    <Tooltip content={<CumulativeTooltip />} />
                    <Line
                      type="monotone"
                      dataKey="cumulativePnL"
                      name="Cumulative P&L"
                      stroke="#2196f3"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* --------------------------------------------------------------
              4. Daily P&L Bar Chart
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              Daily P&L
            </h4>
            <div className="h-56">
              {dailyBarData.length === 0 ? (
                <EmptyChart message="No daily P&L data" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={dailyBarData}
                    margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
                  >
                    <XAxis
                      dataKey="trade_date"
                      tickFormatter={(v: string) => formatShortDate(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                    />
                    <YAxis
                      tickFormatter={(v: number) => formatCurrency(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                      width={70}
                    />
                    <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
                    <Tooltip content={<DailyTooltip />} />
                    <Bar dataKey="daily_pnl" radius={[2, 2, 0, 0]}>
                      {dailyBarData.map((entry, idx) => (
                        <Cell
                          key={idx}
                          fill={entry.daily_pnl >= 0 ? '#00c853' : '#ff1744'}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* --------------------------------------------------------------
              5. Per-Pair Breakdown (Horizontal Bar)
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              P&L by Pair
            </h4>
            <div className="h-56 overflow-y-auto">
              {pairBreakdown.length === 0 ? (
                <EmptyChart message="No pair data" />
              ) : (
                <ResponsiveContainer
                  width="100%"
                  height={Math.max(pairBreakdown.length * 28, 200)}
                >
                  <BarChart
                    data={pairBreakdown}
                    layout="vertical"
                    margin={{ top: 0, right: 20, bottom: 0, left: 5 }}
                  >
                    <XAxis
                      type="number"
                      tickFormatter={(v: number) => formatCurrency(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="pair"
                      tick={{ fill: '#a1a1aa', fontSize: 10 }}
                      axisLine={false}
                      tickLine={false}
                      width={80}
                    />
                    <ReferenceLine x={0} stroke="#3f3f46" strokeDasharray="4 4" />
                    <Tooltip content={<PairTooltip />} />
                    <Bar dataKey="pnl" radius={[0, 4, 4, 0]}>
                      {pairBreakdown.map((entry, idx) => (
                        <Cell
                          key={idx}
                          fill={entry.pnl >= 0 ? '#00c853' : '#ff1744'}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* --------------------------------------------------------------
              6. Per-Exchange Split (Pie Chart)
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              Exchange Split
            </h4>
            <div className="h-56 flex items-center justify-center">
              {exchangePieData.length === 0 ? (
                <EmptyChart message="No exchange data" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={exchangePieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={40}
                      outerRadius={70}
                      paddingAngle={3}
                      dataKey="value"
                      nameKey="name"
                      label={({ name, percent }: { name?: string; percent?: number }) =>
                        `${name ?? ''} ${((percent ?? 0) * 100).toFixed(0)}%`
                      }
                    >
                      {exchangePieData.map((entry, idx) => (
                        <Cell key={idx} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip content={<ExchangeTooltip />} />
                    <Legend
                      wrapperStyle={{ fontSize: 11, color: '#a1a1aa' }}
                      formatter={(value: string) => (
                        <span className="text-xs text-zinc-400">{value}</span>
                      )}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* --------------------------------------------------------------
              7. Drawdown Chart (Area, filled red)
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              Drawdown
            </h4>
            <div className="h-56">
              {drawdownData.length === 0 ? (
                <EmptyChart message="No drawdown data" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={drawdownData}
                    margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
                  >
                    <defs>
                      <linearGradient id="drawdownFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ff1744" stopOpacity={0.05} />
                        <stop offset="100%" stopColor="#ff1744" stopOpacity={0.35} />
                      </linearGradient>
                    </defs>
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(v: string) => formatShortDate(v)}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                    />
                    <YAxis
                      tickFormatter={(v: number) => `${v.toFixed(1)}%`}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                      width={50}
                      domain={['dataMin', 0]}
                    />
                    <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
                    <Tooltip content={<DrawdownTooltip />} />
                    <Area
                      type="monotone"
                      dataKey="drawdown"
                      stroke="#ff1744"
                      strokeWidth={1.5}
                      fill="url(#drawdownFill)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* --------------------------------------------------------------
              8. Win Rate Over Time (Rolling 10-trade)
          -------------------------------------------------------------- */}
          <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
            <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">
              Win Rate (Rolling 10-Trade)
            </h4>
            <div className="h-56">
              {winRateOverTime.length === 0 ? (
                <EmptyChart message="Need at least 10 closed trades" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={winRateOverTime}
                    margin={{ top: 5, right: 20, bottom: 5, left: 10 }}
                  >
                    <XAxis
                      dataKey="tradeIndex"
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                      label={{
                        value: 'Trade #',
                        position: 'insideBottomRight',
                        offset: -5,
                        fill: '#52525b',
                        fontSize: 10,
                      }}
                    />
                    <YAxis
                      tickFormatter={(v: number) => `${v}%`}
                      tick={{ fill: '#71717a', fontSize: 10 }}
                      axisLine={{ stroke: '#27272a' }}
                      tickLine={false}
                      width={45}
                      domain={[0, 100]}
                    />
                    <ReferenceLine
                      y={50}
                      stroke="#ffd600"
                      strokeDasharray="4 4"
                      label={{
                        value: '50%',
                        fill: '#ffd600',
                        fontSize: 10,
                        position: 'right',
                      }}
                    />
                    <Tooltip content={<WinRateTooltip />} />
                    <Line
                      type="monotone"
                      dataKey="winRate"
                      name="Win Rate"
                      stroke="#2196f3"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
