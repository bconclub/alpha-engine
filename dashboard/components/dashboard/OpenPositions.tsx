'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { Badge } from '@/components/ui/Badge';
import {
  formatCurrency,
  formatNumber,
  formatPercentage,
  formatLeverage,
  getExchangeLabel,
  getPositionTypeLabel,
  cn,
} from '@/lib/utils';

function PositionProgressBar({
  entry,
  current,
  sl,
  tp,
}: {
  entry: number;
  current: number;
  sl?: number;
  tp?: number;
}) {
  if (!sl || !tp || tp === sl) {
    return null;
  }

  // Calculate position between SL and TP
  const range = tp - sl;
  const position = ((current - sl) / range) * 100;
  const clamped = Math.max(0, Math.min(100, position));
  const entryPosition = ((entry - sl) / range) * 100;
  const clampedEntry = Math.max(0, Math.min(100, entryPosition));

  return (
    <div className="mt-2">
      <div className="relative h-2 bg-zinc-800 rounded-full overflow-visible">
        {/* SL zone (red side) */}
        <div className="absolute left-0 h-full w-[3px] bg-[#ff1744] rounded-l-full" />
        {/* TP zone (green side) */}
        <div className="absolute right-0 h-full w-[3px] bg-[#00c853] rounded-r-full" />
        {/* Entry marker */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-1 h-3 bg-zinc-500 rounded-sm"
          style={{ left: `${clampedEntry}%` }}
        />
        {/* Current price indicator */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full border-2 border-white bg-[#0d1117]"
          style={{ left: `${clamped}%`, transform: `translate(-50%, -50%)` }}
        />
      </div>
      <div className="flex justify-between mt-1 text-[9px] font-mono">
        <span className="text-[#ff1744]">SL {formatNumber(sl)}</span>
        <span className="text-[#00c853]">TP {formatNumber(tp)}</span>
      </div>
    </div>
  );
}

function durationSince(timestamp: string): string {
  const ms = Date.now() - new Date(timestamp).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h ${remMins}m`;
}

export function OpenPositions() {
  const { openPositions } = useSupabase();

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Open Positions
      </h3>

      {!openPositions || openPositions.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No open positions</p>
      ) : (
        <div className="space-y-3 max-h-[500px] overflow-y-auto pr-1">
          {openPositions.map((pos) => {
            const entryPrice = pos.entry_price;
            const currentPrice = pos.current_price ?? entryPrice;
            const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice) * 100 : 0;
            const adjustedPnlPct = pos.position_type === 'short' ? -pnlPct : pnlPct;
            const isProfit = adjustedPnlPct >= 0;
            const leverageStr = formatLeverage(pos.leverage);
            const positionPnl = pos.pnl ?? (currentPrice - entryPrice) * pos.amount * (pos.position_type === 'short' ? -1 : 1);

            // TP/SL distances
            const tpDistance = pos.take_profit
              ? Math.abs((pos.take_profit - currentPrice) / currentPrice * 100)
              : null;
            const slDistance = pos.stop_loss
              ? Math.abs((currentPrice - pos.stop_loss) / currentPrice * 100)
              : null;

            return (
              <div
                key={pos.id}
                className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-4"
              >
                {/* Header row */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'w-2 h-2 rounded-full',
                        pos.position_type === 'short' ? 'bg-[#ff1744]' : 'bg-[#00c853]',
                      )}
                    />
                    <span className="text-sm font-bold text-white">
                      {getPositionTypeLabel(pos.position_type)} {pos.pair}
                    </span>
                    <span className="text-[10px] text-zinc-500">| {getExchangeLabel(pos.exchange)}</span>
                  </div>
                  {pos.leverage > 1 && (
                    <Badge variant="warning">{leverageStr}</Badge>
                  )}
                </div>

                {/* Price grid */}
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs mb-2">
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Entry</span>
                    <span className="font-mono text-zinc-300">${formatNumber(entryPrice)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Current</span>
                    <span className="font-mono text-zinc-300">${formatNumber(currentPrice)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-500">P&L</span>
                    <span className={cn('font-mono font-medium', isProfit ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                      {isProfit ? '+' : ''}{formatCurrency(positionPnl)} ({formatPercentage(adjustedPnlPct)})
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Duration</span>
                    <span className="font-mono text-zinc-400">{durationSince(pos.opened_at)}</span>
                  </div>
                  {pos.take_profit != null && (
                    <div className="flex justify-between">
                      <span className="text-zinc-500">TP</span>
                      <span className="font-mono text-[#00c853]">
                        ${formatNumber(pos.take_profit)}
                        {tpDistance != null && ` (${tpDistance.toFixed(1)}% away)`}
                      </span>
                    </div>
                  )}
                  {pos.stop_loss != null && (
                    <div className="flex justify-between">
                      <span className="text-zinc-500">SL</span>
                      <span className="font-mono text-[#ff1744]">
                        ${formatNumber(pos.stop_loss)}
                        {slDistance != null && ` (${slDistance.toFixed(1)}% away)`}
                      </span>
                    </div>
                  )}
                </div>

                {/* Progress bar */}
                <PositionProgressBar
                  entry={entryPrice}
                  current={currentPrice}
                  sl={pos.stop_loss ?? undefined}
                  tp={pos.take_profit ?? undefined}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
