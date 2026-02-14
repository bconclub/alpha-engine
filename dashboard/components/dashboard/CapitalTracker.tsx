'use client';

import { useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatShortDate } from '@/lib/utils';

interface CapitalDataPoint {
  timestamp: string;
  label: string;
  capital: number;
}

export function CapitalTracker() {
  const { trades, botStatus } = useSupabase();

  const capitalData = useMemo<CapitalDataPoint[]>(() => {
    if (trades.length === 0 || !botStatus) return [];

    // Sort trades chronologically (oldest first)
    const sorted = [...trades].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
    );

    // Calculate starting capital by subtracting cumulative P&L from current capital
    const totalPnL = sorted.reduce((sum, t) => sum + t.pnl, 0);
    const startingCapital = botStatus.capital - totalPnL;

    let runningCapital = startingCapital;
    const dataPoints: CapitalDataPoint[] = [
      {
        timestamp: sorted[0].timestamp,
        label: formatShortDate(sorted[0].timestamp),
        capital: startingCapital,
      },
    ];

    for (const trade of sorted) {
      runningCapital += trade.pnl;
      dataPoints.push({
        timestamp: trade.timestamp,
        label: formatShortDate(trade.timestamp),
        capital: runningCapital,
      });
    }

    return dataPoints;
  }, [trades, botStatus]);

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Capital Over Time
      </h3>

      {capitalData.length === 0 ? (
        <div className="h-64 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No trade data available</p>
        </div>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={capitalData}>
              <defs>
                <linearGradient id="capitalGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="label"
                tick={{ fill: '#71717a', fontSize: 11 }}
                axisLine={{ stroke: '#27272a' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#71717a', fontSize: 11 }}
                axisLine={{ stroke: '#27272a' }}
                tickLine={false}
                tickFormatter={(v: number) => formatCurrency(v)}
                width={90}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #27272a',
                  borderRadius: '8px',
                  color: '#e4e4e7',
                  fontSize: '13px',
                }}
                formatter={(value) => [formatCurrency(Number(value ?? 0)), 'Capital']}
                labelStyle={{ color: '#a1a1aa' }}
              />
              <Area
                type="monotone"
                dataKey="capital"
                stroke="#10b981"
                strokeWidth={2}
                fill="url(#capitalGradient)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
