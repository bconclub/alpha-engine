'use client';

import { useMemo } from 'react';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatPnL } from '@/lib/utils';

interface ScatterPoint {
  index: number;
  price: number;
  pair: string;
  side: 'buy' | 'sell';
  pnl: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: ScatterPoint }>;
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const data = payload[0].payload;

  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm">
      <p className="font-medium text-white">{data.pair}</p>
      <p className="text-zinc-400">
        Side: <span className={data.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}>{data.side.toUpperCase()}</span>
      </p>
      <p className="text-zinc-400">Price: {formatCurrency(data.price)}</p>
      <p className="text-zinc-400">
        P&L: <span className={data.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatPnL(data.pnl)}</span>
      </p>
    </div>
  );
}

export function TradeScatter() {
  const { trades } = useSupabase();

  const { buys, sells } = useMemo(() => {
    if (trades.length === 0) return { buys: [], sells: [] };

    // Sort by timestamp ascending for sequential indexing
    const sorted = [...trades].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );

    const buyPoints: ScatterPoint[] = [];
    const sellPoints: ScatterPoint[] = [];

    sorted.forEach((trade, i) => {
      const point: ScatterPoint = {
        index: i + 1,
        price: trade.price,
        pair: trade.pair,
        side: trade.side,
        pnl: trade.pnl,
      };

      if (trade.side === 'buy') {
        buyPoints.push(point);
      } else {
        sellPoints.push(point);
      }
    });

    return { buys: buyPoints, sells: sellPoints };
  }, [trades]);

  const hasData = buys.length > 0 || sells.length > 0;

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Trade Distribution
      </h3>

      {!hasData ? (
        <div className="h-80 flex items-center justify-center">
          <p className="text-sm text-zinc-500">No trade data available</p>
        </div>
      ) : (
        <div className="h-80">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis
                dataKey="index"
                name="Trade #"
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
                label={{
                  value: 'Trade #',
                  position: 'insideBottom',
                  offset: -2,
                  fill: '#52525b',
                  fontSize: 11,
                }}
              />
              <YAxis
                dataKey="price"
                name="Price"
                tickFormatter={(value: number) => formatCurrency(value)}
                tick={{ fill: '#71717a', fontSize: 12 }}
                axisLine={{ stroke: '#3f3f46' }}
                tickLine={{ stroke: '#3f3f46' }}
                width={80}
              />
              <Tooltip content={<CustomTooltip />} />
              <Scatter
                name="Buys"
                data={buys}
                fill="#10b981"
                shape="circle"
                r={4}
              />
              <Scatter
                name="Sells"
                data={sells}
                fill="#ef4444"
                shape="circle"
                r={4}
              />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
