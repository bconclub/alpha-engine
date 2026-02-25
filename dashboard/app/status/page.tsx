'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractBase(pair: string): string {
  return pair.includes('/') ? pair.split('/')[0] : pair;
}

function timeSince(ts: string | undefined): string {
  if (!ts) return '—';
  const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// Badge component
function Badge({ children, color }: { children: React.ReactNode; color: 'green' | 'red' | 'amber' | 'zinc' }) {
  const colors = {
    green: 'bg-[#00c853]/10 text-[#00c853] border-[#00c853]/20',
    red: 'bg-[#ff1744]/10 text-[#ff1744] border-[#ff1744]/20',
    amber: 'bg-amber-400/10 text-amber-400 border-amber-400/20',
    zinc: 'bg-zinc-800/50 text-zinc-400 border-zinc-700/50',
  };
  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border', colors[color])}>
      {children}
    </span>
  );
}

// Cooldown pill
function CooldownPill({ label, seconds }: { label: string; seconds: number }) {
  const active = seconds > 0;
  return (
    <div className={cn(
      'flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-mono',
      active ? 'bg-[#ff1744]/10 text-[#ff1744]' : 'bg-zinc-800/30 text-zinc-600',
    )}>
      <span className={cn('w-1.5 h-1.5 rounded-full', active ? 'bg-[#ff1744] animate-pulse' : 'bg-zinc-700')} />
      {label}: {active ? `${seconds}s` : '0'}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function StatusPage() {
  const { botStatus } = useSupabase();

  const diag = botStatus?.diagnostics ?? null;
  const lastUpdated = botStatus?.timestamp ?? botStatus?.created_at;

  const pairEntries = useMemo(() => {
    if (!diag?.pairs) return [];
    return Object.entries(diag.pairs).sort(([a], [b]) => a.localeCompare(b));
  }, [diag?.pairs]);

  // ---------------------------------------------------------------------------
  // No data state
  // ---------------------------------------------------------------------------
  if (!botStatus) {
    return (
      <div className="p-6">
        <h1 className="text-lg font-medium text-white mb-4">Bot Diagnostics</h1>
        <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-8 text-center">
          <p className="text-sm text-zinc-500">Waiting for bot status data...</p>
          <p className="text-[10px] text-zinc-600 mt-1">Make sure the bot is running and connected to Supabase</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-medium text-white">Bot Diagnostics</h1>
        <span className="text-[10px] font-mono text-zinc-600">
          Updated {timeSince(lastUpdated)}
        </span>
      </div>

      {/* Top row: 3 status cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Health */}
        <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className={cn(
              'w-2.5 h-2.5 rounded-full',
              botStatus.bot_state === 'running' ? 'bg-[#00c853] animate-pulse' : 'bg-[#ff1744]',
            )} />
            <span className="text-xs font-medium text-zinc-300 uppercase tracking-wider">Health</span>
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">State</span>
              <span className={cn(
                'text-[11px] font-mono font-medium',
                botStatus.bot_state === 'running' ? 'text-[#00c853]' : 'text-[#ff1744]',
              )}>
                {(botStatus.bot_state ?? 'unknown').toUpperCase()}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">Last Scan</span>
              <span className="text-[11px] font-mono text-zinc-300">
                {diag ? `${diag.last_scan_ago_s}s ago` : '—'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">Uptime</span>
              <span className="text-[11px] font-mono text-zinc-300">
                {formatUptime(botStatus.uptime_seconds)}
              </span>
            </div>
          </div>
        </div>

        {/* Pause Status */}
        <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-zinc-300 uppercase tracking-wider">Status</span>
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">Paused</span>
              <Badge color={diag?.paused?.is_paused ? 'red' : 'green'}>
                {diag?.paused?.is_paused ? 'YES' : 'NO'}
              </Badge>
            </div>
            {diag?.paused?.reason && (
              <div className="mt-1">
                <span className="text-[9px] font-mono text-[#ff1744]/70 break-all">
                  {diag.paused.reason}
                </span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">Positions</span>
              <span className="text-[11px] font-mono text-zinc-300">
                {diag?.positions?.open ?? '?'} / {diag?.positions?.max ?? '?'}
              </span>
            </div>
            {diag?.positions?.pairs && diag.positions.pairs.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1">
                {diag.positions.pairs.map((p) => (
                  <Badge key={p} color="amber">{extractBase(p)}</Badge>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Balance */}
        <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-zinc-300 uppercase tracking-wider">Balance</span>
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between items-center">
              <span className="text-[10px] text-zinc-500">Delta</span>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-mono text-zinc-300">
                  ${diag?.balance?.delta?.toFixed(2) ?? '—'}
                </span>
                <span className={cn('text-[9px]', diag?.balance?.delta_min_trade ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                  {diag?.balance?.delta_min_trade ? '✓' : '✗'}
                </span>
              </div>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[10px] text-zinc-500">Binance</span>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-mono text-zinc-300">
                  ${diag?.balance?.binance?.toFixed(2) ?? '—'}
                </span>
                <span className={cn('text-[9px]', diag?.balance?.binance_min_trade ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                  {diag?.balance?.binance_min_trade ? '✓' : '✗'}
                </span>
              </div>
            </div>
            <div className="flex justify-between">
              <span className="text-[10px] text-zinc-500">Market Regime</span>
              <Badge color={
                botStatus.market_regime === 'CHOPPY' ? 'red'
                : botStatus.market_regime?.startsWith('TRENDING') ? 'green'
                : 'zinc'
              }>
                {botStatus.market_regime ?? '—'}
              </Badge>
            </div>
          </div>
        </div>
      </div>

      {/* Per-pair diagnostics */}
      <div>
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-3">
          Per-Pair Diagnostics
        </h2>
        {pairEntries.length === 0 ? (
          <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-6 text-center">
            <p className="text-sm text-zinc-500">
              {diag ? 'No pairs configured' : 'Diagnostics not available — update engine to v3.23+'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {pairEntries.map(([pair, d]) => {
              const hasAnyCooldown = d.cooldowns.sl > 0 || d.cooldowns.reversal > 0
                || d.cooldowns.streak > 0 || d.cooldowns.phantom > 0;
              const skipColor: 'green' | 'amber' | 'zinc' =
                d.skip_reason === 'NONE' ? (d.in_position ? 'zinc' : 'green')
                : 'amber';
              const skipLabel = d.skip_reason === 'NONE'
                ? (d.in_position ? `IN ${(d.position_side ?? 'TRADE').toUpperCase()}` : 'READY')
                : d.skip_reason;

              return (
                <div
                  key={pair}
                  className={cn(
                    'bg-[#0d1117] border rounded-xl p-4',
                    hasAnyCooldown ? 'border-[#ff1744]/20' : d.in_position ? 'border-amber-400/20' : 'border-zinc-800',
                  )}
                >
                  {/* Pair header + skip reason */}
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-white">{extractBase(pair)}</span>
                    <Badge color={skipColor}>{skipLabel}</Badge>
                  </div>

                  {/* Cooldowns */}
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    <CooldownPill label="SL" seconds={d.cooldowns.sl} />
                    <CooldownPill label="REV" seconds={d.cooldowns.reversal} />
                    <CooldownPill label="STREAK" seconds={d.cooldowns.streak} />
                    <CooldownPill label="PHANTOM" seconds={d.cooldowns.phantom} />
                  </div>

                  {/* Signal state */}
                  <div className="flex gap-3 pt-2 border-t border-zinc-800/50">
                    <div className="flex items-center gap-1">
                      <span className="text-[9px] text-zinc-500">Bull</span>
                      <span className={cn(
                        'text-[11px] font-mono font-medium',
                        d.signals.bull_count >= 3 ? 'text-[#00c853]'
                          : d.signals.bull_count >= 1 ? 'text-[#ffd600]'
                          : 'text-zinc-600',
                      )}>
                        {d.signals.bull_count}/4
                      </span>
                    </div>
                    <div className="flex items-center gap-1">
                      <span className="text-[9px] text-zinc-500">Bear</span>
                      <span className={cn(
                        'text-[11px] font-mono font-medium',
                        d.signals.bear_count >= 3 ? 'text-[#ff1744]'
                          : d.signals.bear_count >= 1 ? 'text-[#ffd600]'
                          : 'text-zinc-600',
                      )}>
                        {d.signals.bear_count}/4
                      </span>
                    </div>
                    {d.signals.rsi != null && (
                      <div className="flex items-center gap-1">
                        <span className="text-[9px] text-zinc-500">RSI</span>
                        <span className={cn(
                          'text-[11px] font-mono',
                          d.signals.rsi < 35 ? 'text-[#00c853]'
                            : d.signals.rsi > 65 ? 'text-[#ff1744]'
                            : 'text-zinc-400',
                        )}>
                          {d.signals.rsi}
                        </span>
                      </div>
                    )}
                    {d.signals.momentum != null && (
                      <div className="flex items-center gap-1">
                        <span className="text-[9px] text-zinc-500">Mom</span>
                        <span className={cn(
                          'text-[11px] font-mono',
                          Math.abs(d.signals.momentum) >= 0.15 ? 'text-[#00c853]' : 'text-zinc-400',
                        )}>
                          {d.signals.momentum >= 0 ? '+' : ''}{d.signals.momentum}%
                        </span>
                      </div>
                    )}
                    {d.signals.trend_15m && (
                      <div className="flex items-center gap-1">
                        <span className="text-[9px] text-zinc-500">15m</span>
                        <span className={cn(
                          'text-[10px] font-mono',
                          d.signals.trend_15m === 'up' ? 'text-[#00c853]'
                            : d.signals.trend_15m === 'down' ? 'text-[#ff1744]'
                            : 'text-zinc-400',
                        )}>
                          {d.signals.trend_15m === 'up' ? '↑' : d.signals.trend_15m === 'down' ? '↓' : '→'}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
