'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { getSupabase } from '@/lib/supabase';
import { cn, formatPnL } from '@/lib/utils';
import type { Trade } from '@/lib/types';

// ─── Types ────────────────────────────────────────────────────────────────────

interface PairSignal {
  shortPair: string;
  fullPair: string;
  trades: number;
  wins: number;
  winRate: number;
  pnl: number;
  streak: string;
  streakTrades: Trade[];
  topExit: string;
  worstExit: string;
  signal: string;
  signalType: 'good' | 'warn' | 'bad' | 'neutral';
  config: { sl: number; tp: number; trail: number };
  recommendations: string[];
}

interface BrainLogEntry {
  id: string;
  created_at: string;
  command: string;
  params: Record<string, unknown>;
  executed: boolean;
  result: string | null;
}

// ─── Config ───────────────────────────────────────────────────────────────────

const PAIR_CONFIG: Record<string, { sl: number; tp: number; trail: number }> = {
  BTC: { sl: 0.30, tp: 1.50, trail: 0.20 },
  ETH: { sl: 0.35, tp: 1.50, trail: 0.20 },
  XRP: { sl: 0.40, tp: 2.00, trail: 0.20 },
  SOL: { sl: 0.35, tp: 1.50, trail: 0.20 },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function shortPair(pair: string): string {
  return pair.split('/')[0];
}

function classifyExit(reason: string | undefined): string {
  if (!reason) return '?';
  const r = reason.toUpperCase();
  if (r.includes('TRAIL')) return 'TRAIL';
  if (r.includes('STOP') || r.includes('SL') || r.includes('STOP-LOSS') || r.includes('STOP_LOSS')) return 'SL';
  if (r.includes('BREAKEVEN') || r.includes('BREAK-EVEN') || r.includes('BE EXIT')) return 'BE';
  if (r.includes('EXPIR')) return 'EXPIRY';
  if (r.includes('TIMEOUT') || r.includes('MAX HOLD') || r.includes('MAX_HOLD')) return 'TIMEOUT';
  if (r.includes('FLAT') || r.includes('FLATLINE')) return 'FLAT';
  if (r.includes('REVERSAL') || r.includes('SIGNAL')) return 'REV';
  if (r.includes('TP') || r.includes('TAKE PROFIT') || r.includes('TAKE_PROFIT') || r.includes('TARGET')) return 'TP';
  if (r.includes('PULLBACK') || r.includes('DECAY')) return 'PB';
  if (r.includes('DUST')) return 'DUST';
  return '?';
}

function holdMinutes(opened: string, closed: string | null | undefined): number {
  if (!closed) return 0;
  return (new Date(closed).getTime() - new Date(opened).getTime()) / 60000;
}

function buildPairSignal(trades: Trade[], sp: string): PairSignal {
  const pairTrades = trades.filter((t) => shortPair(t.pair) === sp);
  const wins = pairTrades.filter((t) => t.pnl > 0);
  const winRate = pairTrades.length > 0 ? (wins.length / pairTrades.length) * 100 : 0;
  const pnl = pairTrades.reduce((s, t) => s + t.pnl, 0);
  const fullPair = pairTrades[0]?.pair || sp;

  const last5 = pairTrades.slice(0, 5);
  const streak = last5.map((t) => (t.pnl > 0 ? 'W' : 'L')).join('');

  const exitMap: Record<string, { count: number; wins: number }> = {};
  for (const t of pairTrades) {
    const ex = classifyExit(t.reason);
    if (!exitMap[ex]) exitMap[ex] = { count: 0, wins: 0 };
    exitMap[ex].count++;
    if (t.pnl > 0) exitMap[ex].wins++;
  }
  const exitEntries = Object.entries(exitMap).filter(([, d]) => d.count >= 2);
  exitEntries.sort((a, b) => (b[1].wins / b[1].count) - (a[1].wins / a[1].count));
  const topExit = exitEntries[0] ? `${exitEntries[0][0]} ${((exitEntries[0][1].wins / exitEntries[0][1].count) * 100).toFixed(0)}% WR` : '\u2014';
  const worstExit = exitEntries.length > 1
    ? `${exitEntries[exitEntries.length - 1][0]} ${((exitEntries[exitEntries.length - 1][1].wins / exitEntries[exitEntries.length - 1][1].count) * 100).toFixed(0)}% WR`
    : '\u2014';

  const recommendations: string[] = [];
  let signal = '';
  let signalType: PairSignal['signalType'] = 'neutral';

  const longs = pairTrades.filter((t) => t.position_type === 'long');
  const shorts = pairTrades.filter((t) => t.position_type === 'short');
  const longWR = longs.length >= 3 ? (longs.filter((t) => t.pnl > 0).length / longs.length) * 100 : -1;
  const shortWR = shorts.length >= 3 ? (shorts.filter((t) => t.pnl > 0).length / shorts.length) * 100 : -1;

  if (pairTrades.length >= 5 && winRate === 0) {
    signal = `0% win rate on ${pairTrades.length} trades \u2014 disable`;
    signalType = 'bad';
    recommendations.push('Disable this pair');
  } else if (pairTrades.length >= 5 && winRate < 20) {
    signal = `${winRate.toFixed(0)}% WR \u2014 reduce allocation or widen SL`;
    signalType = 'bad';
    recommendations.push('Widen SL');
  } else if (pairTrades.length >= 5 && winRate >= 40) {
    signal = `${winRate.toFixed(0)}% WR \u2014 performing well`;
    signalType = 'good';
  } else if (pairTrades.length >= 3) {
    signal = `${winRate.toFixed(0)}% WR on ${pairTrades.length} trades`;
    signalType = winRate >= 30 ? 'warn' : 'bad';
  } else {
    signal = `Only ${pairTrades.length} trades \u2014 not enough data`;
    signalType = 'neutral';
  }

  if (longWR >= 0 && shortWR >= 0 && Math.abs(longWR - shortWR) > 20) {
    if (shortWR > longWR) {
      recommendations.push(`Bias shorts (${shortWR.toFixed(0)}% vs ${longWR.toFixed(0)}% long)`);
    } else {
      recommendations.push(`Bias longs (${longWR.toFixed(0)}% vs ${shortWR.toFixed(0)}% short)`);
    }
  }

  const slData = exitMap['SL'];
  if (slData && slData.count >= 3 && slData.wins === 0) {
    recommendations.push('SL exits 0% WR \u2014 review SL distance');
  }

  const config = PAIR_CONFIG[sp] || { sl: 0.35, tp: 1.50, trail: 0.20 };

  return { shortPair: sp, fullPair, trades: pairTrades.length, wins: wins.length, winRate, pnl, streak, streakTrades: last5, topExit, worstExit, signal, signalType, config, recommendations };
}

// ─── Component ────────────────────────────────────────────────────────────────

export function SentinelTab() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [applyingPair, setApplyingPair] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [brainLog, setBrainLog] = useState<BrainLogEntry[]>([]);

  const fetchTrades = useCallback(async () => {
    setLoading(true);
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data, error } = await sb
        .from('trades')
        .select('*')
        .eq('status', 'closed')
        .order('created_at', { ascending: false })
        .limit(2000);
      if (error) throw error;

      const normalized: Trade[] = (data || []).map((row: Record<string, unknown>) => ({
        id: String(row.id),
        timestamp: String(row.opened_at || row.created_at),
        closed_at: row.closed_at ? String(row.closed_at) : null,
        pair: String(row.pair || ''),
        side: String(row.side || 'buy') as 'buy' | 'sell',
        price: Number(row.entry_price || 0),
        exit_price: row.exit_price ? Number(row.exit_price) : null,
        amount: Number(row.amount || 0),
        cost: Number(row.cost || 0),
        strategy: String(row.strategy || ''),
        pnl: Number(row.pnl || 0),
        pnl_pct: row.pnl_pct ? Number(row.pnl_pct) : undefined,
        status: 'closed' as const,
        exchange: String(row.exchange || 'delta') as Trade['exchange'],
        leverage: Number(row.leverage || 1),
        position_type: String(row.position_type || 'spot') as Trade['position_type'],
        reason: row.reason ? String(row.reason) : undefined,
        order_id: row.order_id ? String(row.order_id) : undefined,
      }));
      setTrades(normalized);
    } catch (err) {
      console.error('Failed to fetch trades:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchBrainLog = useCallback(async () => {
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data } = await sb
        .from('bot_commands')
        .select('*')
        .eq('command', 'update_pair_config')
        .order('created_at', { ascending: false })
        .limit(20);
      setBrainLog((data as BrainLogEntry[]) || []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchTrades(); fetchBrainLog(); }, [fetchTrades, fetchBrainLog]);

  const pairs = useMemo(() => Array.from(new Set(trades.map((t) => shortPair(t.pair)))).filter(Boolean).sort(), [trades]);
  const signals = useMemo(() => { const built = pairs.map((p) => buildPairSignal(trades, p)); built.sort((a, b) => b.winRate - a.winRate); return built; }, [trades, pairs]);
  const totalPnl = useMemo(() => trades.reduce((s, t) => s + t.pnl, 0), [trades]);
  const totalWins = useMemo(() => trades.filter((t) => t.pnl > 0).length, [trades]);
  const overallWR = trades.length > 0 ? (totalWins / trades.length) * 100 : 0;

  const handleApply = useCallback(async (s: PairSignal) => {
    setApplyingPair(s.shortPair);
    try {
      const sb = getSupabase();
      if (!sb) throw new Error('No connection');
      const params: Record<string, unknown> = { pair: s.fullPair, sl: s.config.sl, tp: s.config.tp, trail_activate: s.config.trail };
      for (const rec of s.recommendations) {
        if (rec.includes('Bias shorts')) params.bias = 'short';
        else if (rec.includes('Bias longs')) params.bias = 'long';
        else if (rec.includes('Disable')) params.enabled = false;
        else if (rec.includes('Widen SL')) params.sl = Math.min(s.config.sl + 0.10, 0.60);
      }
      const { error } = await sb.from('bot_commands').insert({ command: 'update_pair_config', params });
      if (error) throw error;
      setFeedback({ type: 'success', message: `Sent config update for ${s.shortPair}` });
      fetchBrainLog();
    } catch (err) {
      setFeedback({ type: 'error', message: err instanceof Error ? err.message : 'Failed' });
    } finally {
      setApplyingPair(null);
      setTimeout(() => setFeedback(null), 4000);
    }
  }, [fetchBrainLog]);

  if (loading) {
    return <div className="flex items-center justify-center min-h-[40vh]"><div className="text-zinc-500 text-sm">Loading...</div></div>;
  }

  return (
    <div className="space-y-4">
      {feedback && (
        <div className={cn(
          'rounded-lg px-4 py-2.5 text-sm font-medium',
          feedback.type === 'success' ? 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20' : 'bg-red-400/10 text-red-400 border border-red-400/20',
        )}>{feedback.message}</div>
      )}

      <div className="flex items-center gap-6 text-sm font-mono">
        <span className="text-zinc-500">{trades.length} trades</span>
        <span className={overallWR >= 40 ? 'text-emerald-400' : 'text-red-400'}>{overallWR.toFixed(0)}% WR</span>
        <span className={totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>{formatPnL(totalPnl)}</span>
        <button onClick={() => { fetchTrades(); fetchBrainLog(); }} className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-700 hover:text-white transition-colors ml-auto">Refresh</button>
      </div>

      <div className="space-y-3">
        {signals.map((s) => (
          <PairRow key={s.shortPair} signal={s} onApply={handleApply} applying={applyingPair === s.shortPair} />
        ))}
      </div>

      {brainLog.length > 0 && (
        <div className="bg-card border border-zinc-800 rounded-xl p-4">
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider mb-3">Sentinel Log</p>
          <div className="space-y-1.5">
            {brainLog.map((entry) => {
              const params = entry.params || {};
              const pair = shortPair(String(params.pair || '?'));
              const changes = Object.entries(params).filter(([k]) => k !== 'pair').map(([k, v]) => `${k}=${String(v)}`).join(' ');
              return (
                <div key={entry.id} className="flex items-center gap-3 text-xs font-mono">
                  <span className="text-zinc-600">{new Date(entry.created_at).toLocaleDateString()}</span>
                  <span className="text-zinc-300 font-medium">{pair}</span>
                  <span className="text-zinc-500">{changes}</span>
                  <span className={entry.executed ? 'text-emerald-500' : 'text-amber-500'}>{entry.executed ? 'done' : 'pending'}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Pair Row ─────────────────────────────────────────────────────────────────

function PairRow({ signal: s, onApply, applying }: { signal: PairSignal; onApply: (s: PairSignal) => void; applying: boolean }) {
  const borderColor = s.signalType === 'good' ? 'border-emerald-800/40' : s.signalType === 'bad' ? 'border-red-800/40' : s.signalType === 'warn' ? 'border-amber-800/40' : 'border-zinc-800';

  return (
    <div className={cn('bg-card border rounded-xl p-4', borderColor)}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-base font-bold text-white">{s.shortPair}</span>
          <span className="text-xs font-mono text-zinc-500">{s.trades} trades</span>
          <span className={cn('text-xs font-mono font-medium', s.winRate >= 40 ? 'text-emerald-400' : s.winRate >= 25 ? 'text-amber-400' : 'text-red-400')}>{s.winRate.toFixed(0)}% WR</span>
        </div>
        <span className={cn('text-sm font-mono font-bold', s.pnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>{formatPnL(s.pnl)}</span>
      </div>

      <div className="flex items-center gap-1.5 mb-3">
        <span className="text-[10px] text-zinc-600 mr-1">LAST 5</span>
        {s.streakTrades.map((t) => {
          const win = t.pnl > 0;
          const exit = classifyExit(t.reason);
          const hold = holdMinutes(t.timestamp, t.closed_at);
          return (
            <div key={t.id} className={cn('flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono', win ? 'bg-emerald-400/10 text-emerald-400' : 'bg-red-400/10 text-red-400')} title={`${win ? 'Win' : 'Loss'} \xB7 ${exit} \xB7 ${hold.toFixed(0)}m \xB7 ${formatPnL(t.pnl)}`}>
              <span className="font-bold">{win ? 'W' : 'L'}</span>
              <span className={win ? 'text-emerald-500/60' : 'text-red-500/60'}>{exit}</span>
            </div>
          );
        })}
        {s.streakTrades.length === 0 && <span className="text-[10px] text-zinc-600">no trades</span>}
      </div>

      <div className="flex items-center gap-4 mb-3 text-xs font-mono">
        <span className="text-zinc-500">Best: <span className="text-emerald-400">{s.topExit}</span></span>
        <span className="text-zinc-500">Worst: <span className="text-red-400">{s.worstExit}</span></span>
      </div>

      <div className={cn('rounded-lg px-3 py-2 text-xs font-mono', s.signalType === 'good' ? 'bg-emerald-400/5 text-emerald-400' : s.signalType === 'bad' ? 'bg-red-400/5 text-red-400' : s.signalType === 'warn' ? 'bg-amber-400/5 text-amber-400' : 'bg-zinc-800/50 text-zinc-400')}>{s.signal}</div>

      {s.recommendations.length > 0 && (
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {s.recommendations.map((rec, i) => (<span key={i} className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] font-mono text-amber-400">{rec}</span>))}
          </div>
          <button onClick={() => onApply(s)} disabled={applying} className="shrink-0 rounded-lg bg-amber-500/20 px-3 py-1.5 text-xs font-bold text-amber-400 border border-amber-500/40 hover:bg-amber-500/30 hover:text-amber-300 transition-all disabled:opacity-50">{applying ? '...' : 'Apply'}</button>
        </div>
      )}
    </div>
  );
}
