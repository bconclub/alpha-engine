'use client';

import { useEffect, useState, useMemo, useRef } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatCurrency, formatPnL, cn } from '@/lib/utils';
import type { Deposit } from '@/lib/types';

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

type DepositRange = '24h' | '7d' | '30d' | 'all';

function DepositsPopover({ deposits, inrRate }: { deposits: Deposit[]; inrRate: number }) {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState<DepositRange>('7d');
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const filtered = useMemo(() => {
    if (range === 'all') return deposits;
    const now = Date.now();
    const ms: Record<string, number> = { '24h': 86400000, '7d': 604800000, '30d': 2592000000 };
    const cutoff = now - (ms[range] ?? 0);
    return deposits.filter((d) => new Date(d.created_at).getTime() >= cutoff);
  }, [deposits, range]);

  const total = filtered.reduce((s, d) => s + d.amount, 0);

  // Mini bar chart data: group by date
  const chartData = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const d of filtered) {
      const date = new Date(d.created_at).toISOString().slice(0, 10);
      byDate.set(date, (byDate.get(date) ?? 0) + d.amount);
    }
    return Array.from(byDate.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([date, amount]) => ({ date, amount }));
  }, [filtered]);

  const maxAmount = Math.max(...chartData.map((d) => d.amount), 1);

  return (
    <div ref={ref} className="relative inline-block">
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          'px-2 py-0.5 rounded text-[9px] font-medium transition-colors border',
          open
            ? 'bg-zinc-700 text-white border-zinc-600'
            : 'text-zinc-500 hover:text-zinc-300 border-zinc-700 hover:border-zinc-600',
        )}
      >
        Deposits
      </button>
      {open && (
        <div className="absolute top-full mt-1.5 right-0 z-50 w-72 bg-[#0d1117] border border-zinc-700 rounded-lg shadow-xl p-3 space-y-2.5">
          {/* Range tabs */}
          <div className="flex gap-1">
            {(['24h', '7d', '30d', 'all'] as DepositRange[]).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={cn(
                  'px-1.5 py-0.5 rounded text-[9px] font-medium transition-colors',
                  range === r ? 'bg-zinc-700 text-white' : 'text-zinc-500 hover:text-zinc-300',
                )}
              >
                {r === 'all' ? 'ALL' : r.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Total */}
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-sm font-bold text-[#2196f3]">
              {formatCurrency(total)}
            </span>
            {inrRate > 0 && (
              <span className="text-[9px] text-zinc-500 font-mono">
                {'\u20B9'}{Math.round(total * inrRate).toLocaleString('en-IN')}
              </span>
            )}
            <span className="text-[9px] text-zinc-500">{filtered.length} deposit{filtered.length !== 1 ? 's' : ''}</span>
          </div>

          {/* Mini bar chart */}
          {chartData.length > 0 && (
            <div className="flex items-end gap-0.5 h-10">
              {chartData.map((d) => (
                <div
                  key={d.date}
                  className="flex-1 bg-[#2196f3]/40 rounded-t-sm hover:bg-[#2196f3]/70 transition-colors"
                  style={{ height: `${Math.max(8, (d.amount / maxAmount) * 100)}%` }}
                  title={`${d.date}: ${formatCurrency(d.amount)}`}
                />
              ))}
            </div>
          )}

          {/* Deposit list */}
          <div className="max-h-36 overflow-y-auto space-y-1">
            {filtered.length === 0 ? (
              <p className="text-[10px] text-zinc-500 text-center py-2">No deposits</p>
            ) : (
              filtered.map((d) => (
                <div key={d.id} className="flex items-center justify-between text-[10px]">
                  <div className="flex items-center gap-1.5">
                    <span className="text-zinc-500 font-mono">
                      {new Date(d.created_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}
                    </span>
                    <span className="text-zinc-400 capitalize">{d.exchange}</span>
                  </div>
                  <span className="font-mono text-[#2196f3]">{formatCurrency(d.amount)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function LiveStatusBar() {
  const { botStatus, isConnected, pnlByExchange, trades, dailyPnL, deposits } = useSupabase();

  const bybitConnected = botStatus?.bybit_connected || (Number(botStatus?.bybit_balance ?? 0) > 0);
  const deltaConnected = botStatus?.delta_connected || (Number(botStatus?.delta_balance ?? 0) > 0);
  const krakenConnected = botStatus?.kraken_connected || (Number(botStatus?.kraken_balance ?? 0) > 0);
  const botState = botStatus?.bot_state ?? (isConnected ? 'running' : 'paused');

  // ── Market Regime ──────────────────────────────────────────────
  const regime = botStatus?.market_regime ?? 'SIDEWAYS';
  const chopScore = botStatus?.chop_score ?? 0;
  const atrRatio = botStatus?.atr_ratio ?? 1;
  const netChange = botStatus?.net_change_30m ?? 0;
  const regimeSince = botStatus?.regime_since;

  const regimeConfig: Record<string, { label: string; icon: string; bg: string; text: string; pulse?: boolean }> = {
    TRENDING_UP:   { label: 'TRENDING UP',   icon: '↗', bg: 'bg-emerald-500/15 border-emerald-500/30', text: 'text-emerald-400' },
    TRENDING_DOWN: { label: 'TRENDING DOWN', icon: '↘', bg: 'bg-red-500/15 border-red-500/30',     text: 'text-red-400' },
    SIDEWAYS:      { label: 'SIDEWAYS',      icon: '↔', bg: 'bg-amber-500/15 border-amber-500/30',  text: 'text-amber-400' },
    CHOPPY:        { label: 'CHOPPY',        icon: '⚡', bg: 'bg-red-500/20 border-red-500/40',      text: 'text-red-400', pulse: true },
  };
  const rc = regimeConfig[regime] ?? regimeConfig.SIDEWAYS;

  const regimeDuration = useMemo(() => {
    if (!regimeSince) return '';
    const elapsed = Math.max(0, Math.floor((Date.now() - new Date(regimeSince).getTime()) / 1000));
    if (elapsed < 60) return `${elapsed}s`;
    if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m`;
    return `${Math.floor(elapsed / 3600)}h ${Math.floor((elapsed % 3600) / 60)}m`;
  }, [regimeSince]);

  const bybitPnl = pnlByExchange.find((e) => e.exchange === 'bybit');
  const deltaPnl = pnlByExchange.find((e) => e.exchange === 'delta');
  const krakenPnl = pnlByExchange.find((e) => e.exchange === 'kraken');

  const bybitBalance = Number(botStatus?.bybit_balance ?? 0);
  const deltaBalance = Number(botStatus?.delta_balance ?? 0);
  const krakenBalance = Number(botStatus?.kraken_balance ?? 0);
  const deltaBalanceInr = botStatus?.delta_balance_inr;

  const exchangeSum = bybitBalance + deltaBalance + krakenBalance;
  const totalCapital = exchangeSum > 0 ? exchangeSum : (botStatus?.capital || 0);
  const inrRate = botStatus?.inr_usd_rate ?? 86.5;
  const capitalInr = Math.round(totalCapital * inrRate);

  const openPositionCount = botStatus?.open_positions ?? 0;

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
    let fees = 0;
    let grossPnl = 0;
    for (const t of trades) {
      if (t.status !== 'closed') continue;
      const tradeTime = new Date(t.timestamp).getTime();
      if (tradeTime >= cutoffMs) {
        pnl += t.pnl ?? 0;
        total++;
        if ((t.pnl ?? 0) > 0) wins++;
        const tradeFees = (t.entry_fee ?? 0) + (t.exit_fee ?? 0);
        fees += tradeFees;
        grossPnl += t.gross_pnl != null ? t.gross_pnl : ((t.pnl ?? 0) + tradeFees);
      }
    }
    const winRate = total > 0 ? (wins / total) * 100 : 0;
    return { pnl, total, wins, losses: total - wins, winRate, fees, grossPnl };
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

  // Count distinct strategies from all trades (not just open)
  const liveStrategyCount = useMemo(() => {
    const strategies = new Set(trades.map((t) => t.strategy));
    return strategies.size || 1;
  }, [trades]);

  const lastHeartbeat = botStatus?.timestamp;
  const [isStale, setIsStale] = useState(true);
  useEffect(() => {
    const check = () => {
      if (!lastHeartbeat) { setIsStale(true); return; }
      setIsStale(Date.now() - new Date(lastHeartbeat).getTime() > 420_000);
    };
    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, [lastHeartbeat]);

  // Human-readable "last updated" label
  const staleLabel = useMemo(() => {
    if (!lastHeartbeat) return 'No data';
    const ago = Math.max(0, Math.floor((Date.now() - new Date(lastHeartbeat).getTime()) / 1000));
    if (ago < 60) return `${ago}s ago`;
    if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
    return `${Math.floor(ago / 3600)}h ${Math.floor((ago % 3600) / 60)}m ago`;
  }, [lastHeartbeat, isStale]); // re-evaluate when isStale ticks

  const sparkColor = pnlStats.pnl >= 0 ? '#00c853' : '#ff1744';

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-4">

      {/* ═══ MOBILE LAYOUT ═══ */}
      <div className="flex flex-col gap-2 md:hidden">

        {/* Row 1 — Balance cards (only show live exchanges with balance) */}
        <div className="grid grid-cols-2 gap-2">
          {bybitConnected && bybitBalance > 0 && (
            <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2">
              <div className="flex items-center gap-1 mb-1">
                <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-[#00c853] animate-pulse" />
                <span className="text-[10px] font-semibold text-[#f7a600] truncate">BYBIT</span>
              </div>
              <span className="font-mono text-sm text-white">{formatCurrency(bybitBalance)}</span>
            </div>
          )}

          {deltaConnected && deltaBalance > 0 && (
            <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2">
              <div className="flex items-center gap-1 mb-1">
                <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-[#00c853] animate-pulse" />
                <span className="text-[10px] font-semibold text-[#00d2ff] truncate">DELTA</span>
              </div>
              <span className="font-mono text-sm text-white">{formatCurrency(deltaBalance)}</span>
            </div>
          )}

          {krakenConnected && krakenBalance > 0 && (
            <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2">
              <div className="flex items-center gap-1 mb-1">
                <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-[#00c853] animate-pulse" />
                <span className="text-[10px] font-semibold text-[#7B61FF] truncate">KRAKEN</span>
              </div>
              <span className="font-mono text-sm text-white">{formatCurrency(krakenBalance)}</span>
            </div>
          )}

          {/* Capital */}
          <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg px-2.5 py-2 text-center">
            <div className="text-[9px] uppercase tracking-wider text-zinc-500 mb-1">Capital</div>
            <span className="font-mono text-sm font-bold text-white">{formatCurrency(totalCapital)}</span>
            {capitalInr > 0 && (
              <div className="text-[9px] text-zinc-500 font-mono">{'\u20B9'}{capitalInr.toLocaleString('en-IN')}</div>
            )}
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
              <DepositsPopover deposits={deposits} inrRate={inrRate} />
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              {pnlStats.total > 0 ? (
                <>
                  <div className="flex items-baseline gap-1.5">
                    <span className={cn(
                      'font-mono text-base font-bold',
                      pnlStats.pnl >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
                    )}>
                      {pnlStats.pnl >= 0 ? '+' : ''}{formatCurrency(pnlStats.pnl)}
                    </span>
                    <span className="text-[9px] text-zinc-500 font-mono">
                      {pnlStats.pnl >= 0 ? '+' : '-'}{'\u20B9'}{Math.abs(Math.round(pnlStats.pnl * inrRate)).toLocaleString('en-IN')}
                    </span>
                  </div>
                  <div className="text-[9px] text-zinc-500 font-mono mt-0.5">
                    {pnlStats.wins}W / {pnlStats.losses}L · {pnlStats.winRate.toFixed(0)}% WR · {pnlStats.total} trades
                  </div>
                  {pnlStats.fees > 0 && (
                    <div className="text-[9px] font-mono mt-0.5">
                      <span className={pnlStats.grossPnl >= 0 ? 'text-[#00c853]/60' : 'text-[#ff1744]/60'}>
                        {pnlStats.grossPnl >= 0 ? '+' : ''}{formatCurrency(pnlStats.grossPnl)} P&L
                      </span>
                      <span className="text-zinc-600"> · </span>
                      <span className="text-zinc-500">${pnlStats.fees.toFixed(2)} fees</span>
                    </div>
                  )}
                </>
              ) : (
                <span className="text-xs text-zinc-500">No trades</span>
              )}
            </div>
            <MiniSparkline data={sparklineData} color={sparkColor} />
          </div>
        </div>

        {/* Row 3 — Market Regime */}
        <div className={cn(
          'flex items-center justify-between rounded-lg border px-3 py-2',
          rc.bg,
          rc.pulse && 'animate-pulse',
        )}>
          <div className="flex items-center gap-2">
            <span className={cn('text-base', rc.text)}>{rc.icon}</span>
            <span className={cn('text-xs font-bold tracking-wide', rc.text)}>{rc.label}</span>
            {regime === 'CHOPPY' && (
              <span className="text-[10px] font-semibold text-red-300 ml-1">NO TRADES</span>
            )}
          </div>
          <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-400">
            <span>Chop: {chopScore.toFixed(2)}</span>
            <span>ATR: {atrRatio.toFixed(1)}x</span>
            <span>Net: {netChange >= 0 ? '+' : ''}{netChange.toFixed(2)}%</span>
            {regimeDuration && <span>Since {regimeDuration}</span>}
          </div>
        </div>

        {/* Row 4 — Bot state + uptime + open positions + clock */}
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
            {botState !== 'running' && botStatus?.pause_reason && (
              <span className="text-[9px] text-[#ffd600]/70 font-mono truncate max-w-[120px]">{botStatus.pause_reason}</span>
            )}
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
          {/* Bybit Card */}
          {bybitConnected && bybitBalance > 0 && (
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  bybitConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
                )}
              />
              <span className="text-sm font-semibold text-[#f7a600]">BYBIT</span>
            </div>
            <div className="flex items-baseline gap-2 min-w-0 flex-wrap">
              <span className="font-mono text-lg text-white truncate">{formatCurrency(bybitBalance)}</span>
            </div>
          </div>
          )}

          {/* Delta Card */}
          {deltaConnected && deltaBalance > 0 && (
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  deltaConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
                )}
              />
              <span className="text-sm font-semibold text-[#00d2ff]">DELTA</span>
            </div>
            <div className="flex items-baseline gap-2 min-w-0 flex-wrap">
              <span className="font-mono text-lg text-white truncate">{formatCurrency(deltaBalance)}</span>
              {deltaBalanceInr != null && (
                <span className="text-[10px] text-zinc-500 shrink-0">~{deltaBalanceInr.toLocaleString()}</span>
              )}
            </div>
          </div>
          )}

          {/* Kraken Card */}
          {krakenConnected && krakenBalance > 0 && (
          <div className="flex-1 bg-zinc-900/50 border border-zinc-800 rounded-lg px-4 py-3">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  krakenConnected && !isStale ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
                )}
              />
              <span className="text-sm font-semibold text-[#7B61FF]">KRAKEN</span>
            </div>
            <div className="flex items-baseline gap-2 min-w-0 flex-wrap">
              <span className="font-mono text-lg text-white truncate">{formatCurrency(krakenBalance)}</span>
            </div>
          </div>
          )}

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
              <DepositsPopover deposits={deposits} inrRate={inrRate} />
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
                  <span className="text-[10px] text-zinc-500 font-mono">
                    {pnlStats.pnl >= 0 ? '+' : '-'}{'\u20B9'}{Math.abs(Math.round(pnlStats.pnl * inrRate)).toLocaleString('en-IN')}
                  </span>
                </div>
                <div className="text-[10px] text-zinc-500 font-mono">
                  {pnlStats.wins}W / {pnlStats.losses}L · {pnlStats.winRate.toFixed(0)}% WR · {pnlStats.total} trades
                </div>
                {pnlStats.fees > 0 && (
                  <div className="text-[10px] font-mono">
                    <span className={pnlStats.grossPnl >= 0 ? 'text-[#00c853]/60' : 'text-[#ff1744]/60'}>
                      {pnlStats.grossPnl >= 0 ? '+' : ''}{formatCurrency(pnlStats.grossPnl)} P&L
                    </span>
                    <span className="text-zinc-600"> · </span>
                    <span className="text-zinc-500">${pnlStats.fees.toFixed(2)} fees</span>
                  </div>
                )}
              </>
            ) : (
              <span className="text-xs text-zinc-500">No trades</span>
            )}
          </div>

          {/* Market Regime Card */}
          <div className={cn(
            'flex-1 border rounded-lg px-4 py-3',
            rc.bg,
            rc.pulse && 'animate-pulse',
          )}>
            <div className="flex items-center gap-2 mb-1">
              <span className={cn('text-base', rc.text)}>{rc.icon}</span>
              <span className={cn('text-sm font-bold tracking-wide', rc.text)}>{rc.label}</span>
              {regime === 'CHOPPY' && (
                <span className="text-[10px] font-semibold text-red-300 ml-1">NO TRADES</span>
              )}
            </div>
            <div className="flex flex-col gap-0.5 text-[10px] font-mono text-zinc-400">
              <div className="flex items-center gap-3">
                <span>Chop: {chopScore.toFixed(2)}</span>
                <span>ATR: {atrRatio.toFixed(1)}x</span>
              </div>
              <div className="flex items-center gap-3">
                <span>Net: {netChange >= 0 ? '+' : ''}{netChange.toFixed(2)}%</span>
                {regimeDuration && <span>Since {regimeDuration}</span>}
              </div>
            </div>
          </div>
        </div>

        {/* Center: Total Capital + Bot State */}
        <div className="flex flex-col items-center gap-1 border-x border-zinc-800 px-6">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">Total Capital</span>
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-xl font-bold text-white truncate">
              {formatCurrency(totalCapital)}
            </span>
            {capitalInr > 0 && (
              <span className="text-[10px] text-zinc-500 font-mono">{'\u20B9'}{capitalInr.toLocaleString('en-IN')}</span>
            )}
          </div>
          {(bybitBalance > 0 || deltaBalance > 0) && openPositionCount > 0 && (
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
            {botState !== 'running' && botStatus?.pause_reason && (
              <span className="text-[10px] text-[#ffd600]/70 font-mono max-w-[200px] truncate" title={botStatus.pause_reason}>
                {botStatus.pause_reason}
              </span>
            )}
            {uptimeSeconds > 0 && (
              <span className="text-[10px] text-zinc-500">{formatUptime(uptimeSeconds)}</span>
            )}
          </div>
          {isStale && (
            <span className="text-[9px] font-mono text-red-400/70">Status: {staleLabel}</span>
          )}
        </div>

        {/* Right: Stats + Clock */}
        <div className="flex items-center gap-4">
          <div className="flex flex-col gap-1.5 text-[10px]">
            <div className="flex items-center gap-2">
              <span className="text-zinc-500">Strategies</span>
              <span className="text-[#2196f3] font-mono">{liveStrategyCount}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-zinc-500">Total Trades</span>
              <span className="text-zinc-300 font-mono">{trades.length}</span>
            </div>
          </div>
          <div className="border-l border-zinc-800 pl-4 flex flex-col items-end gap-1">
            <ISTClock />
            <span className="text-[9px] text-zinc-600 font-mono">
              Alpha v{process.env.ALPHA_VERSION ?? '?'}
            </span>
          </div>
        </div>
      </div>

    </div>
  );
}
