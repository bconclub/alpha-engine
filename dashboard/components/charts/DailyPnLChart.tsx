'use client';

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatShortDate } from '@/lib/utils';
import type { DailyPnL } from '@/lib/types';

interface TooltipPayloadEntry {
  dataKey: string;
  value: number;
  color: string;
  payload: DailyPnL;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
      <p className="text-zinc-400 mb-1">{formatShortDate(data.trade_date)}</p>
      <p className={`font-medium ${data.daily_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
        Total: {formatCurrency(data.daily_pnl)}
      </p>
      <p className="text-[#3b82f6]">
        Spot: {formatCurrency(data.spot_pnl)}
      </p>
      <p className="text-[#f97316]">
        Futures: {formatCurrency(data.futures_pnl)}
      </p>
    </div>
  );
}

export function DailyPnLChart() {
  const { dailyPnL } = useSupabase();

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Daily P&L &mdash; Spot vs Futures
      </h3>

      {dailyPnL.length === 0 ? (
        <div className="h-80 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No daily P&L data</p>
        </div>
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={dailyPnL} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <XAxis
                dataKey="trade_date"
                tickFormatter={(value: string) => formatShortDate(value)}
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
              <Legend
                verticalAlign="bottom"
                wrapperStyle={{ color: '#d4d4d8', fontSize: 12, paddingTop: 8 }}
              />
              <Line
                type="monotone"
                dataKey="daily_pnl"
                name="Total P&L"
                stroke="#e4e4e7"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: '#e4e4e7', stroke: '#18181b', strokeWidth: 2 }}
              />
              <Line
                type="monotone"
                dataKey="spot_pnl"
                name="Spot P&L"
                stroke="#3b82f6"
                strokeWidth={1.5}
                strokeDasharray="4 2"
                dot={false}
                activeDot={{ r: 4, fill: '#3b82f6', stroke: '#18181b', strokeWidth: 2 }}
              />
              <Line
                type="monotone"
                dataKey="futures_pnl"
                name="Futures P&L"
                stroke="#f97316"
                strokeWidth={1.5}
                strokeDasharray="4 2"
                dot={false}
                activeDot={{ r: 4, fill: '#f97316', stroke: '#18181b', strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
