'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { Badge } from '@/components/ui/Badge';
import {
  formatNumber,
  formatPnL,
  formatPercentage,
  formatLeverage,
  getPositionTypeLabel,
  getPositionTypeBadgeVariant,
  getPnLColor,
  cn,
} from '@/lib/utils';

function getStatusBadgeVariant(status: string): 'success' | 'danger' | 'default' {
  switch (status) {
    case 'open': return 'success';
    case 'cancelled': return 'danger';
    default: return 'default';
  }
}

export function FuturesPositions() {
  const { futuresPositions } = useSupabase();

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Futures Positions
      </h3>

      {!futuresPositions || futuresPositions.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No futures positions</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
                <th className="pb-2 pr-3 font-medium">Pair</th>
                <th className="pb-2 pr-3 font-medium">Type</th>
                <th className="pb-2 pr-3 font-medium">Leverage</th>
                <th className="pb-2 pr-3 font-medium text-right">Entry Price</th>
                <th className="pb-2 pr-3 font-medium text-right">Leveraged P&L</th>
                <th className="pb-2 pr-3 font-medium text-right">Leveraged P&L %</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {futuresPositions.map((pos, index) => {
                const leverageStr = formatLeverage(pos.leverage);

                return (
                  <tr
                    key={pos.id}
                    className={cn(
                      'border-b border-zinc-800/50 last:border-0',
                      index % 2 === 0 ? 'bg-transparent' : 'bg-zinc-900/30'
                    )}
                  >
                    <td className="py-2.5 pr-3 text-white font-medium whitespace-nowrap">
                      {pos.pair}
                    </td>
                    <td className="py-2.5 pr-3">
                      <Badge variant={getPositionTypeBadgeVariant(pos.position_type)}>
                        {getPositionTypeLabel(pos.position_type)}
                      </Badge>
                    </td>
                    <td className="py-2.5 pr-3">
                      {pos.leverage > 1 ? (
                        <Badge variant="warning">{leverageStr}</Badge>
                      ) : (
                        <span className="text-zinc-600">&mdash;</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-mono text-zinc-300 whitespace-nowrap">
                      {formatNumber(pos.price)}
                    </td>
                    <td className={cn(
                      'py-2.5 pr-3 text-right font-mono whitespace-nowrap',
                      getPnLColor(pos.leveraged_pnl)
                    )}>
                      {formatPnL(pos.leveraged_pnl)}
                    </td>
                    <td className={cn(
                      'py-2.5 pr-3 text-right font-mono whitespace-nowrap',
                      getPnLColor(pos.leveraged_pnl_pct)
                    )}>
                      {formatPercentage(pos.leveraged_pnl_pct)}
                    </td>
                    <td className="py-2.5">
                      <Badge variant={getStatusBadgeVariant(pos.status)}>
                        {pos.status.toUpperCase()}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
