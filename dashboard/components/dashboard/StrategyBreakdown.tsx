'use client';

import { useMemo } from 'react';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { getStrategyColor, formatPnL } from '@/lib/utils';
import type { Strategy } from '@/lib/types';

interface StrategySlice {
  name: Strategy;
  value: number;
  pnl: number;
  color: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: StrategySlice }>;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
      <p className="font-medium text-white">{data.name}</p>
      <p className="text-zinc-400">
        P&L: <span className={data.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatPnL(data.pnl)}</span>
      </p>
      <p className="text-zinc-500">{data.value} trades</p>
    </div>
  );
}

export function StrategyBreakdown() {
  const { trades } = useSupabase();

  const data = useMemo<StrategySlice[]>(() => {
    if (trades.length === 0) return [];

    const grouped = new Map<Strategy, { count: number; pnl: number }>();

    for (const trade of trades) {
      const current = grouped.get(trade.strategy) ?? { count: 0, pnl: 0 };
      current.count += 1;
      current.pnl += trade.pnl;
      grouped.set(trade.strategy, current);
    }

    return Array.from(grouped.entries()).map(([strategy, stats]) => ({
      name: strategy,
      value: stats.count,
      pnl: stats.pnl,
      color: getStrategyColor(strategy),
    }));
  }, [trades]);

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Strategy Breakdown
      </h3>

      {data.length === 0 ? (
        <div className="h-64 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No trade data available</p>
        </div>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={4}
                dataKey="value"
              >
                {data.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip content={<CustomTooltip />} />
              <Legend
                formatter={(value: string) => (
                  <span className="text-zinc-300 text-sm">{value}</span>
                )}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
