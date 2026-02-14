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
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import {
  formatCurrency,
  formatShortDate,
  formatPnL,
  formatPercentage,
  getStrategyColor,
  cn,
} from '@/lib/utils';
import type { DailyPnL } from '@/lib/types';

type TimeRange = '1d' | '7d' | '30d' | 'all';

function filterByRange(data: DailyPnL[], range: TimeRange): DailyPnL[] {
  if (range === 'all') return data;
  const now = Date.now();
  const ms: Record<string, number> = { '1d': 86400000, '7d': 604800000, '30d': 2592000000 };
  const cutoff = now - (ms[range] ?? 0);
  return data.filter((d) => new Date(d.trade_date).getTime() >= cutoff);
}

interface TooltipPayloadEntry {
  dataKey: string;
  value: number;
  color: string;
  payload: DailyPnL;
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadEntry[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const data = payload[0].payload;
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-zinc-400 mb-1">{formatShortDate(data.trade_date)}</p>
      <p className={data.daily_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]'}>
        Total: {formatCurrency(data.daily_pnl)}
      </p>
      <p className="text-[#2196f3]">Spot: {formatCurrency(data.spot_pnl)}</p>
      <p className="text-[#ffd600]">Futures: {formatCurrency(data.futures_pnl)}</p>
    </div>
  );
}

export function PerformancePanel() {
  const { dailyPnL, trades, strategyPerformance, pnlByExchange } = useSupabase();
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRange>('all');

  const filteredDaily = useMemo(() => filterByRange(dailyPnL, timeRange), [dailyPnL, timeRange]);

  // Per-pair P&L
  const pairBreakdown = useMemo(() => {
    const byPair = new Map<string, { pnl: number; trades: number }>();
    for (const t of trades) {
      const cur = byPair.get(t.pair) ?? { pnl: 0, trades: 0 };
      cur.pnl += t.pnl;
      cur.trades += 1;
      byPair.set(t.pair, cur);
    }
    return Array.from(byPair.entries())
      .map(([pair, stats]) => ({ pair, ...stats }))
      .sort((a, b) => b.pnl - a.pnl);
  }, [trades]);

  // Long vs Short
  const longShortPnL = useMemo(() => {
    let longPnl = 0, shortPnl = 0, longCount = 0, shortCount = 0;
    for (const t of trades) {
      if (t.position_type === 'short') {
        shortPnl += t.pnl;
        shortCount++;
      } else {
        longPnl += t.pnl;
        longCount++;
      }
    }
    return [
      { name: 'Long', pnl: longPnl, count: longCount, color: '#00c853' },
      { name: 'Short', pnl: shortPnl, count: shortCount, color: '#ff1744' },
    ];
  }, [trades]);

  // Strategy data for pie
  const strategyData = useMemo(() => {
    const grouped = new Map<string, { count: number; pnl: number }>();
    for (const sp of strategyPerformance) {
      const cur = grouped.get(sp.strategy) ?? { count: 0, pnl: 0 };
      cur.count += sp.total_trades;
      cur.pnl += sp.total_pnl;
      grouped.set(sp.strategy, cur);
    }
    return Array.from(grouped.entries()).map(([strategy, stats]) => ({
      name: strategy,
      value: stats.count,
      pnl: stats.pnl,
      color: getStrategyColor(strategy),
    }));
  }, [strategyPerformance]);

  // Win rate
  const totalWins = trades.filter((t) => t.pnl > 0).length;
  const totalTrades = trades.filter((t) => t.status === 'closed').length;
  const winRate = totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0;

  const totalPnL = trades.reduce((s, t) => s + t.pnl, 0);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="w-full flex items-center justify-between p-5 hover:bg-zinc-800/20 transition-colors"
      >
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Performance
        </h3>
        <div className="flex items-center gap-4">
          <div className="flex gap-3 text-xs">
            <span className={cn('font-mono', totalPnL >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
              {formatPnL(totalPnL)}
            </span>
            <span className="text-zinc-500">|</span>
            <span className="text-zinc-300">{formatPercentage(winRate)} WR</span>
            <span className="text-zinc-500">|</span>
            <span className="text-zinc-400">{totalTrades} trades</span>
          </div>
          <svg
            className={cn('w-4 h-4 text-zinc-500 transition-transform', isCollapsed ? '' : 'rotate-180')}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {!isCollapsed && (
        <div className="px-5 pb-5 space-y-6">
          {/* Time range toggle */}
          <div className="flex gap-1">
            {(['1d', '7d', '30d', 'all'] as TimeRange[]).map((range) => (
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
                {range === '1d' ? 'Today' : range === '7d' ? '7 Days' : range === '30d' ? '30 Days' : 'All Time'}
              </button>
            ))}
          </div>

          {/* P&L Chart */}
          <div className="h-64">
            {filteredDaily.length === 0 ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-sm text-zinc-500">No P&L data for this period</p>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={filteredDaily} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                  <XAxis
                    dataKey="trade_date"
                    tickFormatter={(v: string) => formatShortDate(v)}
                    tick={{ fill: '#71717a', fontSize: 11 }}
                    axisLine={{ stroke: '#27272a' }}
                    tickLine={false}
                  />
                  <YAxis
                    tickFormatter={(v: number) => formatCurrency(v)}
                    tick={{ fill: '#71717a', fontSize: 11 }}
                    axisLine={{ stroke: '#27272a' }}
                    tickLine={false}
                    width={80}
                  />
                  <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ color: '#d4d4d8', fontSize: 11, paddingTop: 8 }} />
                  <Line type="monotone" dataKey="daily_pnl" name="Total" stroke="#e4e4e7" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="spot_pnl" name="Spot" stroke="#2196f3" strokeWidth={1.5} strokeDasharray="4 2" dot={false} />
                  <Line type="monotone" dataKey="futures_pnl" name="Futures" stroke="#ffd600" strokeWidth={1.5} strokeDasharray="4 2" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Bottom grid: exchange split, strategy breakdown, per-pair, long vs short */}
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
            {/* Per-Exchange P&L */}
            <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
              <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">By Exchange</h4>
              <div className="space-y-2">
                {pnlByExchange.map((ex) => (
                  <div key={ex.exchange} className="flex items-center justify-between text-xs">
                    <span className="text-zinc-300 capitalize">{ex.exchange}</span>
                    <span className={cn('font-mono', ex.total_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                      {formatPnL(ex.total_pnl)}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Strategy breakdown */}
            <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
              <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">By Strategy</h4>
              {strategyData.length > 0 ? (
                <div className="h-32">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={strategyData} cx="50%" cy="50%" innerRadius={25} outerRadius={45} paddingAngle={3} dataKey="value">
                        {strategyData.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="text-xs text-zinc-500 text-center py-4">No data</p>
              )}
            </div>

            {/* Per-pair P&L */}
            <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
              <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">By Pair</h4>
              <div className="space-y-1.5 max-h-40 overflow-y-auto">
                {pairBreakdown.map((p) => (
                  <div key={p.pair} className="flex items-center justify-between text-xs">
                    <span className="text-zinc-300">{p.pair}</span>
                    <span className={cn('font-mono', p.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                      {formatPnL(p.pnl)}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Long vs Short */}
            <div className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4">
              <h4 className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">Long vs Short</h4>
              {longShortPnL.some((l) => l.count > 0) ? (
                <div className="h-32">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={longShortPnL} layout="vertical">
                      <XAxis type="number" tick={{ fill: '#71717a', fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => formatCurrency(v)} />
                      <YAxis type="category" dataKey="name" tick={{ fill: '#a1a1aa', fontSize: 11 }} axisLine={false} tickLine={false} width={40} />
                      <Bar dataKey="pnl" radius={[0, 4, 4, 0]}>
                        {longShortPnL.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="text-xs text-zinc-500 text-center py-4">No data</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
