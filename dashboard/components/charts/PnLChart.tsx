'use client';

import { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatShortDate, getStrategyLabel } from '@/lib/utils';
import type { Trade, Strategy } from '@/lib/types';

interface PnLChartProps {
  trades?: Trade[];
  strategy?: Strategy;
}

interface PnLDataPoint {
  date: string;
  pnl: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: PnLDataPoint }>;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
      <p className="text-zinc-400">{data.date}</p>
      <p className={`font-medium ${data.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
        {formatCurrency(data.pnl)}
      </p>
    </div>
  );
}

// getStrategyLabel is now imported from @/lib/utils

export function PnLChart({ trades: tradesProp, strategy }: PnLChartProps) {
  const { trades: contextTrades } = useSupabase();
  const trades = tradesProp ?? contextTrades;

  const data = useMemo<PnLDataPoint[]>(() => {
    if (trades.length === 0) return [];

    let filtered = trades;
    if (strategy) {
      filtered = trades.filter((t) => t.strategy === strategy);
    }

    // Sort by timestamp ascending for cumulative calculation
    const sorted = [...filtered].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );

    let cumulative = 0;
    return sorted.map((trade) => {
      cumulative += trade.pnl;
      return {
        date: formatShortDate(trade.timestamp),
        pnl: cumulative,
      };
    });
  }, [trades, strategy]);

  const finalPnL = data.length > 0 ? data[data.length - 1].pnl : 0;
  const lineColor = finalPnL >= 0 ? '#10b981' : '#ef4444';

  const title = strategy
    ? `Cumulative P&L — ${getStrategyLabel(strategy)}`
    : 'Cumulative P&L';

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        {title}
      </h3>

      {data.length === 0 ? (
        <div className="h-80 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No P&L data</p>
        </div>
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <XAxis
                dataKey="date"
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
              />
              <YAxis
                tickFormatter={(value: number) => formatCurrency(value)}
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
                width={80}
              />
              <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
              <Tooltip content={<CustomTooltip />} />
              <Line
                type="monotone"
                dataKey="pnl"
                stroke={lineColor}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: lineColor, stroke: '#18181b', strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
