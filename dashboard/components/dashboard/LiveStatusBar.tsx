'use client';

import { useEffect, useState, useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatPnL, cn } from '@/lib/utils';

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function ISTClock() {
  const [time, setTime] = useState('');

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const istStr = now.toLocaleTimeString('en-GB', {
        timeZone: 'Asia/Kolkata',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      setTime(istStr + ' IST');
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <span className="font-mono text-xs text-zinc-400">{time}</span>
  );
}

/** Tiny SVG sparkline — no recharts dependency needed for this */
function MiniSparkline({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;

  const width = 100;
  const height = 28;
  const padding = 2;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => {
    const x = padding + (i / (data.length - 1)) * (width - padding * 2);
    const y = padding + (1 - (v - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  });

  // Gradient fill area
  const firstX = padding;
  const lastX = padding + ((data.length - 1) / (data.length - 1)) * (width - padding * 2);
  const areaPoints = `${firstX},${height} ${points.join(' ')} ${lastX},${height}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="shrink-0">
      <defs>
        <linearGradient id={`spark-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#spark-${color.replace('#', '')})`} />
      <polyline
        points={points.join(' ')}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function LiveStatusBar() {
  const { botStatus, isConnected, pnlByExchange, trades, dailyPnL } = useSupabase();

  const binanceConnected = botStatus?.binance_connected || (Number(botStatus?.binance_balance ?? 0) > 0) || isConnected;
  const deltaConnected = botStatus?.delta_connected || (Number(botStatus?.delta_balance ?? 0) > 0) || isConnected;
  const botState = botStatus?.bot_state ?? (isConnected ? 'running' : 'paused');

  const binancePnl = pnlByExchange.find((e) => e.exchange === 'binance');
  const deltaPnl = pnlByExchange.find((e) => e.exchange === 'delta');

  const binanceBalance = Number(botStatus?.binance_balance ?? 0);
  const deltaBalance = Number(botStatus?.delta_balance ?? 0);
  const deltaBalanceInr = botStatus?.delta_balance_inr;

  const hasFreshBalance = binanceBalance > 0 || deltaBalance > 0;
  const totalCapital = hasFreshBalance ? (binanceBalance + deltaBalance) : (botStatus?.capital || 0);

  const openPositionCount = botStatus?.open_positions ?? 0;

  const shortingEnabled = botStatus?.shorting_enabled ?? false;
  const leverageLevel = botStatus?.leverage ?? botStatus?.leverage_level ?? 1;
  const activeStrategiesCount = botStatus?.active_strategy_count ?? botStatus?.active_strategies_count ?? 0;
  const uptimeSeconds = botStatus?.uptime_seconds ?? 0;

  const [pnlRange, setPnlRange] = useState<'24h' | '7d' | '14d' | '30d'>('24h');

  const pnlStats = useMemo(() => {
    const now = Date.now();
    const istOffsetMs = 5.5 * 60 * 60 * 1000;

    let cutoffMs: number;
    if (pnlRange === '24h') {
      const istNow = new Date(now + istOffsetMs);
      const todayIST = istNow.toISOString().slice(0, 10);
      cutoffMs = new Date(todayIST + 'T00:00:00+05:30').getTime();
    } else {
      const days = pnlRange === '7d' ? 7 : pnlRange === '14d' ? 14 : 30;
      cutoffMs = now - days * 24 * 60 * 60 * 1000;
    }

    let pnl = 0;
    let total = 0;
    let wins = 0;
    for (const t of trades) {
      if (t.status !== 'closed') continue;
      const tradeTime = new Date(t.timestamp).getTime();
      if (tradeTime >= cutoffMs) {
        pnl += t.pnl ?? 0;
        total++;
        if ((t.pnl ?? 0) > 0) wins++;
      }
    }
    const winRate = total > 0 ? (wins / total) * 100 : 0;
    return { pnl, total, wins, losses: total - wins, winRate };
  }, [trades, pnlRange]);

  // Build sparkline data: cumulative PnL from dailyPnL for selected range
  const sparklineData = useMemo(() => {
    const now = Date.now();
    const days = pnlRange === '24h' ? 1 : pnlRange === '7d' ? 7 : pnlRange === '14d' ? 14 : 30;
    const cutoffMs = now - days * 24 * 60 * 60 * 1000;

    const filtered = dailyPnL
      .filter((d) => new Date(d.trade_date).getTime() >= cutoffMs)
      .sort((a, b) => a.trade_date.localeCompare(b.trade_date));

    if (filtered.length === 0) return [];

    let cumulative = 0;
    return filtered.map((d) => {
      cumulative += d.daily_pnl;
      return cumulative;
    });
  }, [dailyPnL, pnlRange]);

  const derivedStrategyCount = useMemo(() => {
    if (activeStrategiesCount > 0) return activeStrategiesCount;
    const strategies = new Set(trades.filter((t) => t.status === 'open').map((t) => t.strategy));
    return strategies.size || 1;
  }, [activeStrategiesCount, trades]);

  const lastHeartbeat = botStatus?.timestamp;
  const isStale = useMemo(() => {
    if (!lastHeartbeat) return true;
    return Date.now() - new Date(lastHeartbeat).getTime() > 420_000;
  }, [lastHeartbeat]);

  const sparkColor = pnlStats.pnl >= 0 ? '#00c853' : '#ff1744';

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-4">

      {/* ═══ MOBILE LAYOUT ═══ */}
      <div className="flex flex-col gap-2 md:hidden">

        {/* Row 1 — Three balance cards */}
        <div className="grid grid-cols-3 gap-2">
          {/* Binance */}
          <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2">
            <div className="flex items-center gap-1 mb-1">
              <span className={cn(
                'w-1.5 h-1.5 rounded-full shrink-0',
                binanceConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
              )} />
              <span className="text-[10px] font-semibold text-[#f0b90b] truncate">BINANCE</span>
            </div>
            {binanceBalance > 0 ? (
              <span className="font-mono text-sm text-white">{formatCurrency(binanceBalance)}</span>
            ) : binancePnl ? (
              <span className={cn('font-mono text-xs', binancePnl.total_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                {formatPnL(binancePnl.total_pnl)}
              </span>
            ) : (
              <span className="text-[10px] text-zinc-500">No data</span>
            )}
          </div>

          {/* Delta */}
          <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2">
            <div className="flex items-center gap-1 mb-1">
              <span className={cn(
                'w-1.5 h-1.5 rounded-full shrink-0',
                deltaConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
              )} />
              <span className="text-[10px] font-semibold text-[#00d2ff] truncate">DELTA</span>
            </div>
            {deltaBalance > 0 ? (
              <span className="font-mono text-sm text-white">{formatCurrency(deltaBalance)}</span>
            ) : deltaPnl ? (
              <span className={cn('font-mono text-xs', deltaPnl.total_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                {formatPnL(deltaPnl.total_pnl)}
              </span>
            ) : (
              <span className="text-[10px] text-zinc-500">No data</span>
            )}
          </div>

          {/* Total Capital */}
          <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2 text-center">
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mb-1">Total</div>
            <span className="font-mono text-sm font-bold text-white">{formatCurrency(totalCapital)}</span>
          </div>
        </div>

        {/* Row 2 — PnL with time range + micro sparkline */}
        <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-3 py-2">
          <div className="flex items-center justify-between mb-1.5">
            <div className="flex items-center gap-1">
              {(['24h', '7d', '14d', '30d'] as const).map((range) => (
                <button
                  key={range}
                  onClick={() => setPnlRange(range)}
                  className={cn(
                    'px-1.5 py-0.5 rounded text-[9px] font-medium transition-colors',
                    pnlRange === range
                      ? 'bg-zinc-700 text-white'
                      : 'text-zinc-500 hover:text-zinc-300',
                  )}
                >
                  {range.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              {pnlStats.total > 0 ? (
                <>
                  <span className={cn(
                    'font-mono text-base font-bold',
                    pnlStats.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
                  )}>
                    {pnlStats.pnl >= 0 ? '+' : ''}{formatCurrency(pnlStats.pnl)}
                  </span>
                  <div className="text-[9px] text-zinc-500 font-mono mt-0.5">
                    {pnlStats.wins}W / {pnlStats.losses}L · {pnlStats.winRate.toFixed(0)}% WR · {pnlStats.total} trades
                  </div>
                </>
              ) : (
                <span className="text-xs text-zinc-500">No trades</span>
              )}
            </div>
            <MiniSparkline data={sparklineData} color={sparkColor} />
          </div>
        </div>

        {/* Row 3 — Bot state + uptime + open positions + clock */}
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium',
                botState === 'running'
                  ? 'bg-[#00c853]/10 text-[#00c853]'
                  : 'bg-[#ffd600]/10 text-[#ffd600]',
              )}
            >
              <span className={cn(
                'w-1.5 h-1.5 rounded-full',
                botState === 'running' ? 'bg-[#00c853]' : 'bg-[#ffd600]',
              )} />
              {botState === 'running' ? 'Running' : 'Paused'}
            </span>
            {uptimeSeconds > 0 && (
              <span className="text-[9px] text-zinc-500 font-mono">{formatUptime(uptimeSeconds)}</span>
            )}
            {openPositionCount > 0 && (
              <span className="text-[9px] font-mono text-amber-400">{openPositionCount} open</span>
            )}
          </div>
          <ISTClock />
        </div>
      </div>

      {/* ═══ DESKTOP LAYOUT (unchanged) ═══ */}
      <div className="hidden md:flex md:flex-row md:items-center md:justify-between gap-4">
        {/* Exchange Cards */}
        <div className="flex gap-3 flex-1 min-w-0">
          {/* Binance Card */}
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  binanceConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
                )}
              />
              <span className="text-sm font-semibold text-[#f0b90b]">BINANCE</span>
              <span className="text-[10px] text-zinc-500">(Spot)</span>
            </div>
            {binanceBalance > 0 ? (
              <div className="flex items-baseline gap-2 min-w-0">
                <span className="font-mono text-lg text-white truncate">{formatCurrency(binanceBalance)}</span>
              </div>
            ) : binancePnl ? (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-zinc-500">P&L:</span>
                <span className={cn('font-mono', binancePnl.total_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                  {formatPnL(binancePnl.total_pnl)}
                </span>
              </div>
            ) : (
              <span className="text-xs text-zinc-500">No data</span>
            )}
          </div>

          {/* Delta Card */}
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  deltaConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
                )}
              />
              <span className="text-sm font-semibold text-[#00d2ff]">DELTA</span>
              <span className="text-[10px] text-zinc-500">(Futures)</span>
            </div>
            {deltaBalance > 0 ? (
              <div className="flex items-baseline gap-2 min-w-0 flex-wrap">
                <span className="font-mono text-lg text-white truncate">{formatCurrency(deltaBalance)}</span>
                {deltaBalanceInr != null && (
                  <span className="text-[10px] text-zinc-500 shrink-0">~{deltaBalanceInr.toLocaleString()}</span>
                )}
              </div>
            ) : deltaPnl ? (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-zinc-500">P&L:</span>
                <span className={cn('font-mono', deltaPnl.total_pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]')}>
                  {formatPnL(deltaPnl.total_pnl)}
                </span>
              </div>
            ) : (
              <span className="text-xs text-zinc-500">No data</span>
            )}
          </div>

          {/* P&L Summary Card */}
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-1.5 mb-1">
              {(['24h', '7d', '14d', '30d'] as const).map((range) => (
                <button
                  key={range}
                  onClick={() => setPnlRange(range)}
                  className={cn(
                    'px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors',
                    pnlRange === range
                      ? 'bg-zinc-700 text-white'
                      : 'text-zinc-500 hover:text-zinc-300',
                  )}
                >
                  {range.toUpperCase()}
                </button>
              ))}
            </div>
            {pnlStats.total > 0 ? (
              <>
                <div className="flex items-baseline gap-2">
                  <span className={cn(
                    'font-mono text-lg font-bold',
                    pnlStats.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
                  )}>
                    {pnlStats.pnl >= 0 ? '+' : ''}{formatCurrency(pnlStats.pnl)}
                  </span>
                </div>
                <div className="text-[10px] text-zinc-500 font-mono">
                  {pnlStats.wins}W / {pnlStats.losses}L · {pnlStats.winRate.toFixed(0)}% WR · {pnlStats.total} trades
                </div>
              </>
            ) : (
              <span className="text-xs text-zinc-500">No trades</span>
            )}
          </div>
        </div>

        {/* Center: Total Capital + Bot State */}
        <div className="flex flex-col items-center gap-1 border-x border-zinc-800 px-6">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">Total Capital</span>
          <span className="font-mono text-xl font-bold text-white truncate max-w-full">
            {formatCurrency(totalCapital)}
          </span>
          {hasFreshBalance && openPositionCount > 0 && (
            <span className="text-[10px] font-mono text-amber-400">{openPositionCount} open</span>
          )}
          <div className="flex items-center gap-3 mt-1">
            <span
              className={cn(
                'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium',
                botState === 'running'
                  ? 'bg-[#00c853]/10 text-[#00c853]'
                  : 'bg-[#ffd600]/10 text-[#ffd600]',
              )}
            >
              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full',
                  botState === 'running' ? 'bg-[#00c853]' : 'bg-[#ffd600]',
                )}
              />
              {botState === 'running' ? 'Running' : 'Paused'}
            </span>
            {uptimeSeconds > 0 && (
              <span className="text-[10px] text-zinc-500">{formatUptime(uptimeSeconds)}</span>
            )}
          </div>
        </div>

        {/* Right: Indicators + Clock */}
        <div className="flex items-center gap-4">
          <div className="flex flex-col gap-1.5 text-[10px]">
            <div className="flex items-center gap-2">
              <span className="text-zinc-500">Shorting</span>
              <span className={shortingEnabled ? 'text-[#00c853]' : 'text-zinc-600'}>
                {shortingEnabled ? 'ON' : 'OFF'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-zinc-500">Leverage</span>
              <span className="text-[#ffd600] font-mono">{leverageLevel}x</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-zinc-500">Strategies</span>
              <span className="text-[#2196f3] font-mono">{derivedStrategyCount}</span>
            </div>
          </div>
          <div className="border-l border-zinc-800 pl-4 flex flex-col items-end gap-1">
            <ISTClock />
            <span className="text-[9px] text-zinc-600 font-mono">
              engine v{process.env.ENGINE_VERSION ?? '?'} · dash v{process.env.APP_VERSION ?? '?'}
            </span>
          </div>
        </div>
      </div>

    </div>
  );
}
