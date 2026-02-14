'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { Badge } from '@/components/ui/Badge';
import { formatTimeAgo } from '@/lib/utils';
import type { Strategy } from '@/lib/types';

function getStrategyBadgeVariant(strategy: Strategy | undefined): 'blue' | 'warning' | 'purple' | 'default' {
  switch (strategy) {
    case 'Grid': return 'blue';
    case 'Momentum': return 'warning';
    case 'Arbitrage': return 'purple';
    default: return 'default';
  }
}

export function ActiveStrategy() {
  const { botStatus, strategyLog } = useSupabase();

  const latestLog = strategyLog.length > 0 ? strategyLog[0] : null;
  const activeStrategy = botStatus?.active_strategy;

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Active Strategy
      </h3>

      {activeStrategy ? (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold text-white">{activeStrategy}</span>
            <Badge variant={getStrategyBadgeVariant(activeStrategy)}>
              {activeStrategy}
            </Badge>
          </div>

          {latestLog?.market_condition && (
            <div>
              <Badge variant="default">{latestLog.market_condition}</Badge>
            </div>
          )}

          {latestLog?.reason && (
            <p className="text-sm text-zinc-500">{latestLog.reason}</p>
          )}

          {latestLog?.timestamp && (
            <p className="text-xs text-zinc-600">
              Last switched {formatTimeAgo(latestLog.timestamp)}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-zinc-500">No active strategy</p>
      )}
    </div>
  );
}
