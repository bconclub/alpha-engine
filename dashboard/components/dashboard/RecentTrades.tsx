'use client';

import Link from 'next/link';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { Badge } from '@/components/ui/Badge';
import {
  formatShortDate,
  formatNumber,
  formatPnL,
  getPnLColor,
  cn,
  getExchangeLabel,
  getExchangeColor,
  getPositionTypeLabel,
  getPositionTypeColor,
  formatLeverage,
} from '@/lib/utils';
import type { Strategy } from '@/lib/types';

function getStrategyBadgeVariant(strategy: Strategy): 'blue' | 'warning' | 'purple' | 'default' {
  switch (strategy) {
    case 'Grid': return 'blue';
    case 'Momentum': return 'warning';
    case 'Arbitrage': return 'purple';
    case 'futures_momentum': return 'warning';
    default: return 'default';
  }
}

export function RecentTrades() {
  const { recentTrades } = useSupabase();

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Recent Trades
        </h3>
        <Link
          href="/trades"
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
        >
          View all &rarr;
        </Link>
      </div>

      {recentTrades.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No trades yet</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
                <th className="pb-2 pr-3 font-medium">Time</th>
                <th className="pb-2 pr-3 font-medium">Pair</th>
                <th className="pb-2 pr-3 font-medium">Exchange</th>
                <th className="pb-2 pr-3 font-medium">Side</th>
                <th className="pb-2 pr-3 font-medium">Type</th>
                <th className="pb-2 pr-3 font-medium text-right">Price</th>
                <th className="pb-2 pr-3 font-medium text-right">Amount</th>
                <th className="pb-2 pr-3 font-medium">Strategy</th>
                <th className="pb-2 font-medium text-right">P&L</th>
              </tr>
            </thead>
            <tbody>
              {recentTrades.map((trade, index) => (
                <tr
                  key={trade.id}
                  className={cn(
                    'border-b border-zinc-800/50 last:border-0',
                    index % 2 === 0 ? 'bg-transparent' : 'bg-zinc-900/30'
                  )}
                >
                  <td className="py-2.5 pr-3 text-zinc-400 whitespace-nowrap">
                    {formatShortDate(trade.timestamp)}
                  </td>
                  <td className="py-2.5 pr-3 text-white font-medium whitespace-nowrap">
                    {trade.pair}
                  </td>
                  <td className="py-2.5 pr-3 whitespace-nowrap">
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
                  <td className="py-2.5 pr-3">
                    <Badge variant={trade.side === 'buy' ? 'success' : 'danger'}>
                      {trade.side.toUpperCase()}
                    </Badge>
                  </td>
                  <td className="py-2.5 pr-3 whitespace-nowrap">
                    <span className={cn('text-xs font-medium', getPositionTypeColor(trade.position_type))}>
                      {getPositionTypeLabel(trade.position_type)}
                    </span>
                    {trade.leverage > 1 && (
                      <span className="ml-1 text-xs font-medium text-amber-400">
                        {formatLeverage(trade.leverage)}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 pr-3 text-right font-mono text-zinc-300 whitespace-nowrap">
                    {formatNumber(trade.price)}
                  </td>
                  <td className="py-2.5 pr-3 text-right font-mono text-zinc-300 whitespace-nowrap">
                    {formatNumber(trade.amount, 4)}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Badge variant={getStrategyBadgeVariant(trade.strategy)}>
                      {trade.strategy}
                    </Badge>
                  </td>
                  <td className={cn('py-2.5 text-right font-mono whitespace-nowrap', getPnLColor(trade.pnl))}>
                    {formatPnL(trade.pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
