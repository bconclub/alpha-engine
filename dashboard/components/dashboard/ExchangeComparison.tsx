'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import {
  formatPercentage,
  formatPnL,
  getPnLColor,
  getExchangeLabel,
  getExchangeColor,
  cn,
} from '@/lib/utils';
import type { Exchange } from '@/lib/types';

const exchangeSubtitles: Record<Exchange, string> = {
  binance: '(Spot)',
  delta: '(Futures)',
};

export function ExchangeComparison() {
  const { pnlByExchange } = useSupabase();

  if (!pnlByExchange || pnlByExchange.length === 0) {
    return (
      <div className="grid grid-cols-1 gap-4">
        <div className="bg-card border border-zinc-800 rounded-xl p-5">
          <p className="text-sm text-zinc-500 text-center py-8">No exchange data available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {pnlByExchange.map((ex) => {
        const color = getExchangeColor(ex.exchange);

        return (
          <div
            key={ex.exchange}
            className="bg-card border border-zinc-800 rounded-xl overflow-hidden"
          >
            {/* Colored top accent line */}
            <div className="h-0.5" style={{ backgroundColor: color }} />

            <div className="p-5">
              {/* Header */}
              <div className="mb-4">
                <h3 className="text-sm font-semibold text-white">
                  {getExchangeLabel(ex.exchange)}
                </h3>
                <span className="text-xs text-zinc-500">
                  {exchangeSubtitles[ex.exchange]}
                </span>
              </div>

              {/* Stats */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-zinc-500">Total Trades</span>
                  <span className="text-sm font-medium text-zinc-200">
                    {ex.total_trades}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs text-zinc-500">Win Rate</span>
                  <span className="text-sm font-medium text-zinc-200">
                    {formatPercentage(ex.win_rate_pct)}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs text-zinc-500">Total P&L</span>
                  <span className={cn('text-sm font-medium font-mono', getPnLColor(ex.total_pnl))}>
                    {formatPnL(ex.total_pnl)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
