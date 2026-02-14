'use client';

import { useMemo } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  CartesianGrid,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { getStrategyColor } from '@/lib/utils';
import type { Strategy } from '@/lib/types';

interface WinRateData {
  strategy: Strategy;
  winRate: number;
  wins: number;
  total: number;
  color: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: WinRateData }>;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
      <p className="font-medium text-white">{data.strategy}</p>
      <p className="text-zinc-400">
        Win Rate: <span className="text-white">{data.winRate.toFixed(1)}%</span>
      </p>
      <p className="text-zinc-500">
        {data.wins} wins / {data.total} trades
      </p>
    </div>
  );
}

export function WinRateChart() {
  const { trades } = useSupabase();

  const data = useMemo<WinRateData[]>(() => {
    if (trades.length === 0) return [];

    const grouped = new Map<Strategy, { wins: number; total: number }>();

    for (const trade of trades) {
      const current = grouped.get(trade.strategy) ?? { wins: 0, total: 0 };
      current.total += 1;
      if (trade.pnl > 0) current.wins += 1;
      grouped.set(trade.strategy, current);
    }

    return Array.from(grouped.entries()).map(([strategy, stats]) => ({
      strategy,
      winRate: stats.total > 0 ? (stats.wins / stats.total) * 100 : 0,
      wins: stats.wins,
      total: stats.total,
      color: getStrategyColor(strategy),
    }));
  }, [trades]);

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Win Rate by Strategy
      </h3>

      {data.length === 0 ? (
        <div className="h-80 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No trade data available</p>
        </div>
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
              <XAxis
                dataKey="strategy"
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(value: number) => `${value}%`}
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
                width={50}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
              <Bar dataKey="winRate" radius={[6, 6, 0, 0]} maxBarSize={60}>
                {data.map((entry) => (
                  <Cell key={entry.strategy} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
