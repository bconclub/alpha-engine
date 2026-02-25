'use client';

import { useMemo } from 'react';
import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatPnL, formatShortDate, cn } from '@/lib/utils';

function extractBase(pair: string): string {
  return pair.includes('/') ? pair.split('/')[0] : pair;
}

export default function AnalyticsPage() {
  const { trades } = useSupabase();

  const topTrades = useMemo(() => {
    return trades
      .filter(t => t.status === 'closed' && t.pnl != null && t.pnl > 0)
      .sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0))
      .slice(0, 5);
  }, [trades]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl md:text-2xl font-bold tracking-tight text-white">
        Analytics
      </h1>
      <AnalyticsPanel />

      {/* Top Trades highlight */}
      {topTrades.length > 0 && (
        <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4 md:p-5">
          <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-3">
            Top Trades
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {topTrades.map((trade, idx) => (
              <div
                key={trade.id ?? idx}
                className="bg-[#00c853]/5 border border-[#00c853]/20 rounded-lg p-3"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] font-mono text-zinc-500">#{idx + 1}</span>
                  <span className="text-sm font-bold text-white">{extractBase(trade.pair)}</span>
                </div>
                <div className="text-lg font-bold font-mono text-[#00c853]">
                  {formatPnL(trade.pnl ?? 0)}
                </div>
                {trade.pnl_pct != null && (
                  <div className="text-[10px] font-mono text-[#00c853]/70 mt-0.5">
                    {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct.toFixed(2)}%
                  </div>
                )}
                <div className="text-[10px] text-zinc-500 mt-1">
                  {trade.timestamp ? formatShortDate(trade.timestamp) : '—'}
                </div>
                {trade.exit_reason && (
                  <div className={cn(
                    'inline-block mt-1 px-1.5 py-0.5 rounded text-[9px] font-mono',
                    'bg-zinc-800/50 text-zinc-400 border border-zinc-700/50',
                  )}>
                    {trade.exit_reason}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
