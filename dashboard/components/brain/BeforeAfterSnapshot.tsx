'use client';

import { cn } from '@/lib/utils';
import type { SnapshotStats } from '@/lib/types';
import { formatHold } from '@/lib/brain-utils';

interface Props {
  before: SnapshotStats;
  after: SnapshotStats;
}

function StatRow({ label, beforeVal, afterVal, format, higherIsBetter = true }: {
  label: string;
  beforeVal: number;
  afterVal: number;
  format: (n: number) => string;
  higherIsBetter?: boolean;
}) {
  const diff = afterVal - beforeVal;
  const improved = higherIsBetter ? diff > 0 : diff < 0;
  const unchanged = Math.abs(diff) < 0.001;

  return (
    <div className="grid grid-cols-3 gap-2 items-center py-1.5 border-b border-zinc-800/50 last:border-0">
      <span className="text-[11px] text-zinc-500">{label}</span>
      <span className="text-xs font-mono text-zinc-400 text-center">{format(beforeVal)}</span>
      <div className="flex items-center justify-center gap-1.5">
        <span className="text-xs font-mono text-white">{format(afterVal)}</span>
        {!unchanged && (
          <span className={cn(
            'text-[10px] font-mono',
            improved ? 'text-emerald-400' : 'text-red-400',
          )}>
            {improved ? '\u25B2' : '\u25BC'}
          </span>
        )}
      </div>
    </div>
  );
}

export function BeforeAfterSnapshot({ before, after }: Props) {
  return (
    <div className="bg-[#0d1117] border border-zinc-800/60 rounded-lg p-3 mt-3">
      {/* Column headers */}
      <div className="grid grid-cols-3 gap-2 mb-2">
        <span className="text-[10px] text-zinc-600 uppercase tracking-wider">Metric</span>
        <span className="text-[10px] text-zinc-600 uppercase tracking-wider text-center">
          Before ({before.trade_count})
        </span>
        <span className="text-[10px] text-zinc-600 uppercase tracking-wider text-center">
          After ({after.trade_count})
        </span>
      </div>

      <StatRow
        label="Win Rate"
        beforeVal={before.win_rate}
        afterVal={after.win_rate}
        format={(n) => `${n.toFixed(1)}%`}
      />
      <StatRow
        label="Avg PnL"
        beforeVal={before.avg_pnl}
        afterVal={after.avg_pnl}
        format={(n) => `$${n.toFixed(4)}`}
      />
      <StatRow
        label="Total PnL"
        beforeVal={before.total_pnl}
        afterVal={after.total_pnl}
        format={(n) => `$${n.toFixed(4)}`}
      />
      <StatRow
        label="Avg Hold"
        beforeVal={before.avg_hold_seconds}
        afterVal={after.avg_hold_seconds}
        format={formatHold}
        higherIsBetter={false}
      />

      {/* Exit breakdown */}
      <div className="mt-3 pt-2 border-t border-zinc-800/50">
        <span className="text-[10px] text-zinc-600 uppercase tracking-wider">Exit Breakdown</span>
        <div className="grid grid-cols-2 gap-4 mt-2">
          <div>
            <span className="text-[10px] text-zinc-600 mb-1 block">Before</span>
            <div className="flex flex-wrap gap-1">
              {Object.entries(before.exit_breakdown)
                .sort((a, b) => b[1] - a[1])
                .map(([exit, count]) => (
                  <span key={exit} className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
                    {exit} {count}
                  </span>
                ))}
              {Object.keys(before.exit_breakdown).length === 0 && (
                <span className="text-[10px] text-zinc-600">No data</span>
              )}
            </div>
          </div>
          <div>
            <span className="text-[10px] text-zinc-600 mb-1 block">After</span>
            <div className="flex flex-wrap gap-1">
              {Object.entries(after.exit_breakdown)
                .sort((a, b) => b[1] - a[1])
                .map(([exit, count]) => (
                  <span key={exit} className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-zinc-400">
                    {exit} {count}
                  </span>
                ))}
              {Object.keys(after.exit_breakdown).length === 0 && (
                <span className="text-[10px] text-zinc-600">No data</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Warnings */}
      {(before.trade_count < 50 || after.trade_count < 50) && (
        <div className="mt-2 text-[10px] text-amber-400/70 font-mono">
          {before.trade_count < 50 && `Before: only ${before.trade_count} trades (< 50). `}
          {after.trade_count < 50 && `After: only ${after.trade_count} trades (< 50).`}
        </div>
      )}
    </div>
  );
}
