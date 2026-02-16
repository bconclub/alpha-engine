'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { getSupabase } from '@/lib/supabase';
import { cn, formatPnL, formatPercentage } from '@/lib/utils';
import type { Trade } from '@/lib/types';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
} from 'recharts';

// ─── Types ────────────────────────────────────────────────────────────────────

interface PairAnalysis {
  pair: string;
  shortPair: string; // "BTC", "ETH", etc.
  totalTrades: number;
  wins: number;
  losses: number;
  winRate: number;
  totalPnl: number;
  exitBreakdown: Record<string, { count: number; wins: number; pnl: number }>;
  bestExit: { type: string; winRate: number; pnl: number } | null;
  worstExit: { type: string; winRate: number; pnl: number } | null;
  avgHoldWinners: number; // minutes
  avgHoldLosers: number;
  longWinRate: number;
  shortWinRate: number;
  longCount: number;
  shortCount: number;
  avgWinPnlPct: number;
  avgLossPnlPct: number;
  currentConfig: { sl: number; tp: number; trailActivate: number; phase1: number };
  recommendations: string[];
  recentTrades: Trade[];
}

interface ExitRow {
  type: string;
  count: number;
  wins: number;
  winRate: number;
  totalPnl: number;
  avgHold: number;
  recommendation: string;
}

interface HourlyPnl {
  hour: number;
  label: string;
  pnl: number;
  trades: number;
}

interface DailyPnl {
  date: string;
  pnl: number;
  trades: number;
}

interface BrainLogEntry {
  id: string;
  created_at: string;
  command: string;
  params: Record<string, unknown>;
  executed: boolean;
  result: string | null;
}

// ─── Known config per pair (mirrors scalp.py) ─────────────────────────────────

const PAIR_CONFIG: Record<string, { sl: number; tp: number; trailActivate: number; phase1: number }> = {
  BTC: { sl: 0.30, tp: 1.50, trailActivate: 0.20, phase1: 60 },
  ETH: { sl: 0.35, tp: 1.50, trailActivate: 0.20, phase1: 60 },
  XRP: { sl: 0.40, tp: 2.00, trailActivate: 0.20, phase1: 60 },
  SOL: { sl: 0.35, tp: 1.50, trailActivate: 0.20, phase1: 60 },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function shortPair(pair: string): string {
  return pair.split('/')[0];
}

function classifyExit(reason: string | undefined): string {
  if (!reason) return 'UNKNOWN';
  const r = reason.toUpperCase();
  if (r.includes('TRAIL')) return 'TRAIL';
  if (r.includes('STOP') || r.includes('SL') || r.includes('STOP-LOSS') || r.includes('STOP_LOSS')) return 'SL';
  if (r.includes('BREAKEVEN') || r.includes('BREAK-EVEN') || r.includes('BE EXIT')) return 'BREAKEVEN';
  if (r.includes('EXPIR')) return 'EXPIRY';
  if (r.includes('TIMEOUT') || r.includes('MAX HOLD') || r.includes('MAX_HOLD')) return 'TIMEOUT';
  if (r.includes('FLAT') || r.includes('FLATLINE')) return 'FLAT';
  if (r.includes('REVERSAL') || r.includes('SIGNAL')) return 'REVERSAL';
  if (r.includes('TP') || r.includes('TAKE PROFIT') || r.includes('TAKE_PROFIT') || r.includes('TARGET')) return 'TP';
  if (r.includes('PULLBACK') || r.includes('DECAY')) return 'PULLBACK';
  if (r.includes('DUST')) return 'DUST';
  return 'OTHER';
}

function holdMinutes(opened: string, closed: string | null | undefined): number {
  if (!closed) return 0;
  return (new Date(closed).getTime() - new Date(opened).getTime()) / 60000;
}

function avg(arr: number[]): number {
  if (arr.length === 0) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

// ─── Analysis engine ──────────────────────────────────────────────────────────

function analyzePair(trades: Trade[], pairFilter: string): PairAnalysis {
  const sp = shortPair(pairFilter);
  const pairTrades = trades.filter((t) => shortPair(t.pair) === sp);
  const wins = pairTrades.filter((t) => t.pnl > 0);
  const losses = pairTrades.filter((t) => t.pnl <= 0);

  // Exit breakdown
  const exitBreakdown: Record<string, { count: number; wins: number; pnl: number }> = {};
  for (const t of pairTrades) {
    const exit = classifyExit(t.reason);
    if (!exitBreakdown[exit]) exitBreakdown[exit] = { count: 0, wins: 0, pnl: 0 };
    exitBreakdown[exit].count++;
    if (t.pnl > 0) exitBreakdown[exit].wins++;
    exitBreakdown[exit].pnl += t.pnl;
  }

  let bestExit: PairAnalysis['bestExit'] = null;
  let worstExit: PairAnalysis['worstExit'] = null;
  for (const [type, data] of Object.entries(exitBreakdown)) {
    if (data.count < 1) continue;
    const wr = (data.wins / data.count) * 100;
    if (!bestExit || wr > bestExit.winRate || (wr === bestExit.winRate && data.pnl > bestExit.pnl)) {
      bestExit = { type, winRate: wr, pnl: data.pnl };
    }
    if (!worstExit || wr < worstExit.winRate || (wr === worstExit.winRate && data.pnl < worstExit.pnl)) {
      worstExit = { type, winRate: wr, pnl: data.pnl };
    }
  }

  // Hold times
  const winHolds = wins.map((t) => holdMinutes(t.timestamp, t.closed_at)).filter((m) => m > 0);
  const lossHolds = losses.map((t) => holdMinutes(t.timestamp, t.closed_at)).filter((m) => m > 0);

  // Long vs Short
  const longs = pairTrades.filter((t) => t.position_type === 'long');
  const shorts = pairTrades.filter((t) => t.position_type === 'short');
  const longWins = longs.filter((t) => t.pnl > 0).length;
  const shortWins = shorts.filter((t) => t.pnl > 0).length;

  // Avg winning/losing PnL%
  const winPcts = wins.map((t) => t.pnl_pct ?? 0);
  const lossPcts = losses.map((t) => t.pnl_pct ?? 0);

  const config = PAIR_CONFIG[sp] || { sl: 0.35, tp: 1.50, trailActivate: 0.20, phase1: 60 };

  // Recommendations
  const recommendations: string[] = [];
  const totalTrades = pairTrades.length;
  const winRate = totalTrades > 0 ? (wins.length / totalTrades) * 100 : 0;
  const totalPnl = pairTrades.reduce((s, t) => s + t.pnl, 0);

  if (totalTrades >= 5 && winRate === 0) {
    recommendations.push(`${sp} 0% WR on ${totalTrades} trades — consider disabling`);
  } else if (totalTrades >= 5 && winRate < 25) {
    recommendations.push(`${sp} only ${winRate.toFixed(0)}% WR — reduce allocation or widen SL`);
  }

  if (shorts.length >= 3 && longs.length >= 3) {
    const swr = shorts.length > 0 ? (shortWins / shorts.length) * 100 : 0;
    const lwr = longs.length > 0 ? (longWins / longs.length) * 100 : 0;
    if (swr > lwr + 20) {
      recommendations.push(`${sp} shorts win ${swr.toFixed(0)}% vs longs ${lwr.toFixed(0)}% — bias to shorts`);
    } else if (lwr > swr + 20) {
      recommendations.push(`${sp} longs win ${lwr.toFixed(0)}% vs shorts ${swr.toFixed(0)}% — bias to longs`);
    }
  }

  if (bestExit && bestExit.winRate === 100 && exitBreakdown[bestExit.type]?.count >= 2) {
    if (bestExit.type === 'TRAIL') {
      recommendations.push(`${sp} trail exits are 100% winners — lower trail activation to 0.15%`);
    } else {
      recommendations.push(`${sp} ${bestExit.type} exits are 100% winners (${exitBreakdown[bestExit.type].count} trades)`);
    }
  }

  if (worstExit && worstExit.winRate === 0 && exitBreakdown[worstExit.type]?.count >= 3) {
    recommendations.push(`${sp} ${worstExit.type} exits have 0% WR — review ${worstExit.type.toLowerCase()} settings`);
  }

  if (avg(lossHolds) > 0 && avg(lossHolds) > 15) {
    recommendations.push(`${sp} losers hold avg ${avg(lossHolds).toFixed(0)}min — tighten timeout or SL`);
  }

  return {
    pair: pairFilter,
    shortPair: sp,
    totalTrades,
    wins: wins.length,
    losses: losses.length,
    winRate,
    totalPnl,
    exitBreakdown,
    bestExit,
    worstExit,
    avgHoldWinners: avg(winHolds),
    avgHoldLosers: avg(lossHolds),
    longWinRate: longs.length > 0 ? (longWins / longs.length) * 100 : 0,
    shortWinRate: shorts.length > 0 ? (shortWins / shorts.length) * 100 : 0,
    longCount: longs.length,
    shortCount: shorts.length,
    avgWinPnlPct: avg(winPcts),
    avgLossPnlPct: avg(lossPcts),
    currentConfig: config,
    recommendations,
    recentTrades: pairTrades.slice(0, 5),
  };
}

function analyzeExits(trades: Trade[]): ExitRow[] {
  const types = ['TRAIL', 'SL', 'TP', 'BREAKEVEN', 'EXPIRY', 'TIMEOUT', 'FLAT', 'REVERSAL', 'PULLBACK', 'OTHER'];
  const rows: ExitRow[] = [];

  for (const type of types) {
    const matching = trades.filter((t) => classifyExit(t.reason) === type);
    if (matching.length === 0) continue;
    const wins = matching.filter((t) => t.pnl > 0).length;
    const wr = (wins / matching.length) * 100;
    const pnl = matching.reduce((s, t) => s + t.pnl, 0);
    const holds = matching.map((t) => holdMinutes(t.timestamp, t.closed_at)).filter((m) => m > 0);

    let rec = '';
    if (wr === 100) rec = 'Excellent — increase usage';
    else if (wr >= 60) rec = 'Good — maintain';
    else if (wr >= 40) rec = 'Average — monitor';
    else if (wr > 0) rec = 'Poor — review settings';
    else rec = 'Bad — investigate';

    rows.push({
      type,
      count: matching.length,
      wins,
      winRate: wr,
      totalPnl: pnl,
      avgHold: avg(holds),
      recommendation: rec,
    });
  }

  return rows.sort((a, b) => b.count - a.count);
}

function analyzeByHour(trades: Trade[]): HourlyPnl[] {
  const hours: Record<number, { pnl: number; trades: number }> = {};
  for (let h = 0; h < 24; h++) hours[h] = { pnl: 0, trades: 0 };

  for (const t of trades) {
    const h = new Date(t.timestamp).getUTCHours();
    hours[h].pnl += t.pnl;
    hours[h].trades++;
  }

  return Object.entries(hours).map(([h, data]) => ({
    hour: Number(h),
    label: `${h.padStart(2, '0')}:00`,
    pnl: data.pnl,
    trades: data.trades,
  }));
}

function analyzeByDay(trades: Trade[]): DailyPnl[] {
  const days: Record<string, { pnl: number; trades: number }> = {};

  for (const t of trades) {
    const d = new Date(t.timestamp).toISOString().slice(0, 10);
    if (!days[d]) days[d] = { pnl: 0, trades: 0 };
    days[d].pnl += t.pnl;
    days[d].trades++;
  }

  return Object.entries(days)
    .map(([date, data]) => ({ date, pnl: data.pnl, trades: data.trades }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function BrainPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [applyingPair, setApplyingPair] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [brainLog, setBrainLog] = useState<BrainLogEntry[]>([]);

  // Fetch closed trades
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

      // Normalize to Trade type
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

  // Fetch brain log (update_pair_config commands)
  const fetchBrainLog = useCallback(async () => {
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data } = await sb
        .from('bot_commands')
        .select('*')
        .eq('command', 'update_pair_config')
        .order('created_at', { ascending: false })
        .limit(50);

      setBrainLog((data as BrainLogEntry[]) || []);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchTrades();
    fetchBrainLog();
  }, [fetchTrades, fetchBrainLog]);

  // Compute analysis
  const pairs = useMemo(() => {
    const uniquePairs = Array.from(new Set(trades.map((t) => shortPair(t.pair)))).filter(Boolean);
    return uniquePairs.sort();
  }, [trades]);

  const pairAnalyses = useMemo(() => {
    return pairs.map((p) => analyzePair(trades, p));
  }, [trades, pairs]);

  const exitRows = useMemo(() => analyzeExits(trades), [trades]);
  const hourlyPnl = useMemo(() => analyzeByHour(trades), [trades]);
  const dailyPnl = useMemo(() => analyzeByDay(trades), [trades]);

  // Summary stats
  const totalPnl = useMemo(() => trades.reduce((s, t) => s + t.pnl, 0), [trades]);
  const totalWins = useMemo(() => trades.filter((t) => t.pnl > 0).length, [trades]);
  const overallWinRate = useMemo(
    () => (trades.length > 0 ? (totalWins / trades.length) * 100 : 0),
    [trades, totalWins],
  );
  const bestPair = useMemo(
    () => pairAnalyses.reduce<PairAnalysis | null>((best, p) => (!best || p.totalPnl > best.totalPnl ? p : best), null),
    [pairAnalyses],
  );
  const worstPair = useMemo(
    () => pairAnalyses.reduce<PairAnalysis | null>((worst, p) => (!worst || p.totalPnl < worst.totalPnl ? p : worst), null),
    [pairAnalyses],
  );

  // Hold time stats
  const winnersAvgHold = useMemo(() => {
    const holds = trades.filter((t) => t.pnl > 0).map((t) => holdMinutes(t.timestamp, t.closed_at)).filter((m) => m > 0);
    return avg(holds);
  }, [trades]);
  const losersAvgHold = useMemo(() => {
    const holds = trades.filter((t) => t.pnl <= 0).map((t) => holdMinutes(t.timestamp, t.closed_at)).filter((m) => m > 0);
    return avg(holds);
  }, [trades]);

  // Apply config
  const handleApply = useCallback(
    async (analysis: PairAnalysis) => {
      setApplyingPair(analysis.shortPair);
      try {
        const sb = getSupabase();
        if (!sb) throw new Error('No Supabase connection');

        // Build recommended params from analysis
        const params: Record<string, unknown> = {
          pair: analysis.pair,
          sl: analysis.currentConfig.sl,
          tp: analysis.currentConfig.tp,
          trail_activate: analysis.currentConfig.trailActivate,
        };

        // Apply recommendation-driven adjustments
        for (const rec of analysis.recommendations) {
          if (rec.includes('bias to shorts')) params.bias = 'short';
          else if (rec.includes('bias to longs')) params.bias = 'long';
          else if (rec.includes('consider disabling')) params.enabled = false;
          else if (rec.includes('lower trail activation')) params.trail_activate = 0.15;
          else if (rec.includes('widen SL')) params.sl = Math.min((analysis.currentConfig.sl + 0.10), 0.60);
          else if (rec.includes('tighten timeout')) params.timeout_minutes = 15;
        }

        const { error } = await sb.from('bot_commands').insert({
          command: 'update_pair_config',
          params,
        });
        if (error) throw error;

        setFeedback({ type: 'success', message: `Config update sent for ${analysis.shortPair}` });
        fetchBrainLog();
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to send command';
        setFeedback({ type: 'error', message: msg });
      } finally {
        setApplyingPair(null);
        setTimeout(() => setFeedback(null), 4000);
      }
    },
    [fetchBrainLog],
  );

  // ────────────────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-zinc-500 text-sm">Analyzing trades...</div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl md:text-2xl font-bold tracking-tight text-white">Sentinel</h1>
          <span className="text-xs text-zinc-600 font-mono">{trades.length} closed trades</span>
        </div>
        <button
          onClick={() => { fetchTrades(); fetchBrainLog(); }}
          className="flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700 hover:text-white"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M13.65 2.35A8 8 0 1 0 16 8h-2a6 6 0 1 1-1.76-4.24L10 6h6V0l-2.35 2.35z" fill="currentColor" />
          </svg>
          Refresh
        </button>
      </div>

      {/* Feedback toast */}
      {feedback && (
        <div
          className={cn(
            'rounded-lg px-4 py-3 text-sm font-medium',
            feedback.type === 'success'
              ? 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20'
              : 'bg-red-400/10 text-red-400 border border-red-400/20',
          )}
        >
          {feedback.message}
        </div>
      )}

      {/* ─── 1. TOP SUMMARY BAR ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <SummaryCard label="Total Trades" value={String(trades.length)} />
        <SummaryCard
          label="Win Rate"
          value={`${overallWinRate.toFixed(1)}%`}
          color={overallWinRate >= 50 ? 'green' : overallWinRate >= 30 ? 'yellow' : 'red'}
        />
        <SummaryCard label="Total PnL" value={formatPnL(totalPnl)} color={totalPnl >= 0 ? 'green' : 'red'} />
        <SummaryCard
          label="Top Performer"
          value={bestPair ? bestPair.shortPair : '--'}
          subtitle={bestPair ? `${bestPair.winRate.toFixed(0)}% WR · ${formatPnL(bestPair.totalPnl)}` : undefined}
          color="green"
        />
        <SummaryCard
          label="Needs Work"
          value={worstPair ? worstPair.shortPair : '--'}
          subtitle={worstPair ? `${worstPair.winRate.toFixed(0)}% WR · ${formatPnL(worstPair.totalPnl)}` : undefined}
          color="red"
        />
      </div>

      {/* ─── 2. PER-PAIR CARDS ───────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {pairAnalyses.map((a) => (
          <PairCard key={a.shortPair} analysis={a} onApply={handleApply} applying={applyingPair === a.shortPair} />
        ))}
      </div>

      {/* ─── 3. EXIT ANALYSIS TABLE ──────────────────────────────────────── */}
      <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">Exit Analysis</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs uppercase border-b border-zinc-800">
                <th className="text-left py-2 pr-4">Type</th>
                <th className="text-right py-2 px-3">Count</th>
                <th className="text-right py-2 px-3">Win Rate</th>
                <th className="text-right py-2 px-3">Total PnL</th>
                <th className="text-right py-2 px-3">Avg Hold</th>
                <th className="text-left py-2 pl-4">Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {exitRows.map((row) => (
                <tr key={row.type} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                  <td className="py-2.5 pr-4 font-mono font-medium text-zinc-200">{row.type}</td>
                  <td className="py-2.5 px-3 text-right font-mono text-zinc-300">{row.count}</td>
                  <td className={cn('py-2.5 px-3 text-right font-mono', row.winRate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                    {row.winRate.toFixed(0)}%
                  </td>
                  <td className={cn('py-2.5 px-3 text-right font-mono', row.totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                    {formatPnL(row.totalPnl)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-mono text-zinc-400">{row.avgHold.toFixed(1)}m</td>
                  <td className="py-2.5 pl-4 text-zinc-400 text-xs">{row.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ─── 4. TIME ANALYSIS ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* PnL by Hour */}
        <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
          <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">PnL by Hour (UTC)</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={hourlyPnl} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="label" tick={{ fill: '#71717a', fontSize: 10 }} interval={2} />
                <YAxis tick={{ fill: '#71717a', fontSize: 10 }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: '#9ca3af' }}
                  formatter={(value: unknown) => [formatPnL(Number(value ?? 0)), 'PnL']}
                />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {hourlyPnl.map((entry, i) => (
                    <Cell key={i} fill={entry.pnl >= 0 ? '#00c853' : '#ff1744'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* PnL by Day */}
        <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
          <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">PnL by Day</h2>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dailyPnl} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="date" tick={{ fill: '#71717a', fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis tick={{ fill: '#71717a', fontSize: 10 }} tickFormatter={(v: number) => `$${v.toFixed(2)}`} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: '#9ca3af' }}
                  formatter={(value: unknown) => [formatPnL(Number(value ?? 0)), 'PnL']}
                />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {dailyPnl.map((entry, i) => (
                    <Cell key={i} fill={entry.pnl >= 0 ? '#00c853' : '#ff1744'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Hold time comparison */}
      <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">Hold Time Analysis</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-zinc-500">Winners Avg Hold</p>
            <p className="text-lg font-mono font-medium text-emerald-400">{winnersAvgHold.toFixed(1)} min</p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Losers Avg Hold</p>
            <p className="text-lg font-mono font-medium text-red-400">{losersAvgHold.toFixed(1)} min</p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Difference</p>
            <p className="text-lg font-mono font-medium text-zinc-300">
              {Math.abs(winnersAvgHold - losersAvgHold).toFixed(1)} min
            </p>
          </div>
          <div>
            <p className="text-xs text-zinc-500">Insight</p>
            <p className="text-xs text-zinc-400 mt-1">
              {winnersAvgHold > losersAvgHold
                ? 'Winners hold longer — patience pays'
                : 'Losers hold longer — cut losses faster'}
            </p>
          </div>
        </div>
      </div>

      {/* ─── 5. SENTINEL LOG ──────────────────────────────────────────────── */}
      <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">Sentinel Log</h2>
        {brainLog.length === 0 ? (
          <p className="text-sm text-zinc-600">No config changes applied from Sentinel yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs uppercase border-b border-zinc-800">
                  <th className="text-left py-2 pr-3">Time</th>
                  <th className="text-left py-2 px-3">Pair</th>
                  <th className="text-left py-2 px-3">Changes</th>
                  <th className="text-left py-2 px-3">Status</th>
                  <th className="text-left py-2 pl-3">Result</th>
                </tr>
              </thead>
              <tbody>
                {brainLog.map((entry) => {
                  const params = entry.params || {};
                  const pairName = String(params.pair || '?');
                  const changes = Object.entries(params)
                    .filter(([k]) => k !== 'pair')
                    .map(([k, v]) => `${k}=${String(v)}`)
                    .join(', ');

                  return (
                    <tr key={entry.id} className="border-b border-zinc-800/50">
                      <td className="py-2 pr-3 font-mono text-xs text-zinc-500">
                        {new Date(entry.created_at).toLocaleString()}
                      </td>
                      <td className="py-2 px-3 font-mono text-zinc-300">{shortPair(pairName)}</td>
                      <td className="py-2 px-3 font-mono text-xs text-zinc-400">{changes}</td>
                      <td className="py-2 px-3">
                        <span
                          className={cn(
                            'inline-block px-2 py-0.5 rounded text-xs font-medium',
                            entry.executed ? 'bg-emerald-400/10 text-emerald-400' : 'bg-amber-400/10 text-amber-400',
                          )}
                        >
                          {entry.executed ? 'Executed' : 'Pending'}
                        </span>
                      </td>
                      <td className="py-2 pl-3 text-xs text-zinc-500">{entry.result || '--'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SummaryCard({
  label,
  value,
  subtitle,
  color,
}: {
  label: string;
  value: string;
  subtitle?: string;
  color?: 'green' | 'red' | 'yellow';
}) {
  const colorClass =
    color === 'green'
      ? 'text-emerald-400'
      : color === 'red'
        ? 'text-red-400'
        : color === 'yellow'
          ? 'text-amber-400'
          : 'text-white';

  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-3 md:p-4">
      <p className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</p>
      <p className={cn('text-base md:text-lg font-mono font-bold mt-1', colorClass)}>{value}</p>
      {subtitle && <p className="text-[10px] font-mono text-zinc-500 mt-0.5">{subtitle}</p>}
    </div>
  );
}

function PairCard({
  analysis: a,
  onApply,
  applying,
}: {
  analysis: PairAnalysis;
  onApply: (a: PairAnalysis) => void;
  applying: boolean;
}) {
  const profitable = a.totalPnl >= 0;

  return (
    <div
      className={cn(
        'bg-card border rounded-xl p-4 md:p-5',
        profitable ? 'border-emerald-800/50' : 'border-red-800/50',
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className={cn('w-2 h-2 rounded-full', profitable ? 'bg-emerald-400' : 'bg-red-400')} />
          <h3 className="text-lg font-bold text-white">{a.shortPair}</h3>
          <span className="text-xs font-mono text-zinc-600">{a.pair}</span>
        </div>
        <span className={cn('font-mono font-bold text-sm', profitable ? 'text-emerald-400' : 'text-red-400')}>
          {formatPnL(a.totalPnl)}
        </span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-3 gap-3 mb-4 text-xs">
        <div>
          <p className="text-zinc-500">Trades</p>
          <p className="font-mono font-medium text-zinc-200">{a.totalTrades}</p>
        </div>
        <div>
          <p className="text-zinc-500">Win Rate</p>
          <p className={cn('font-mono font-medium', a.winRate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
            {a.winRate.toFixed(1)}%
          </p>
        </div>
        <div>
          <p className="text-zinc-500">W / L</p>
          <p className="font-mono font-medium text-zinc-200">
            <span className="text-emerald-400">{a.wins}</span>
            {' / '}
            <span className="text-red-400">{a.losses}</span>
          </p>
        </div>
      </div>

      {/* Exit breakdown */}
      <div className="grid grid-cols-2 gap-3 mb-4 text-xs">
        {a.bestExit && (
          <div className="bg-emerald-400/5 border border-emerald-800/30 rounded-lg p-2">
            <p className="text-zinc-500">Best Exit</p>
            <p className="font-mono text-emerald-400">
              {a.bestExit.type}: {a.bestExit.winRate.toFixed(0)}% WR, {formatPnL(a.bestExit.pnl)}
            </p>
          </div>
        )}
        {a.worstExit && (
          <div className="bg-red-400/5 border border-red-800/30 rounded-lg p-2">
            <p className="text-zinc-500">Worst Exit</p>
            <p className="font-mono text-red-400">
              {a.worstExit.type}: {a.worstExit.winRate.toFixed(0)}% WR, {formatPnL(a.worstExit.pnl)}
            </p>
          </div>
        )}
      </div>

      {/* Hold times & direction */}
      <div className="grid grid-cols-2 gap-3 mb-4 text-xs">
        <div>
          <p className="text-zinc-500">Hold Time (W / L)</p>
          <p className="font-mono text-zinc-300">
            <span className="text-emerald-400">{a.avgHoldWinners.toFixed(1)}m</span>
            {' / '}
            <span className="text-red-400">{a.avgHoldLosers.toFixed(1)}m</span>
          </p>
        </div>
        <div>
          <p className="text-zinc-500">Long / Short WR</p>
          <p className="font-mono text-zinc-300">
            <span className="text-emerald-400">{a.longWinRate.toFixed(0)}%</span>
            <span className="text-zinc-600"> ({a.longCount})</span>
            {' / '}
            <span className="text-red-400">{a.shortWinRate.toFixed(0)}%</span>
            <span className="text-zinc-600"> ({a.shortCount})</span>
          </p>
        </div>
        <div>
          <p className="text-zinc-500">Avg Win PnL%</p>
          <p className="font-mono text-emerald-400">{formatPercentage(a.avgWinPnlPct)}</p>
        </div>
        <div>
          <p className="text-zinc-500">Avg Loss PnL%</p>
          <p className="font-mono text-red-400">{formatPercentage(a.avgLossPnlPct)}</p>
        </div>
      </div>

      {/* Last 5 trades */}
      {a.recentTrades.length > 0 && (
        <div className="mb-4">
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1.5">Last {a.recentTrades.length} Trades</p>
          <div className="space-y-1">
            {a.recentTrades.map((t) => {
              const win = t.pnl > 0;
              const exit = t.reason ? classifyExit(t.reason) : '—';
              const hold = t.closed_at ? holdMinutes(t.timestamp, t.closed_at) : 0;
              return (
                <div
                  key={t.id}
                  className={cn(
                    'flex items-center justify-between rounded px-2 py-1 text-[11px] font-mono',
                    win ? 'bg-emerald-400/5' : 'bg-red-400/5',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn('font-medium', win ? 'text-emerald-400' : 'text-red-400')}>
                      {win ? 'W' : 'L'}
                    </span>
                    <span className="text-zinc-500">
                      {t.position_type === 'long' ? 'L' : t.position_type === 'short' ? 'S' : '·'}
                    </span>
                    <span className="text-zinc-400">{exit}</span>
                    <span className="text-zinc-600">{hold > 0 ? `${hold.toFixed(0)}m` : ''}</span>
                  </div>
                  <span className={cn('font-medium', win ? 'text-emerald-400' : 'text-red-400')}>
                    {formatPnL(t.pnl)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Current config */}
      <div className="bg-zinc-800/30 rounded-lg p-2.5 mb-4 text-xs">
        <p className="text-zinc-500 mb-1">Current Config</p>
        <div className="flex flex-wrap gap-3 font-mono text-zinc-300">
          <span>SL: {a.currentConfig.sl}%</span>
          <span>TP: {a.currentConfig.tp}%</span>
          <span>Trail: {a.currentConfig.trailActivate}%</span>
          <span>Phase1: {a.currentConfig.phase1}s</span>
        </div>
      </div>

      {/* Recommendations */}
      {a.recommendations.length > 0 && (
        <div className="bg-amber-400/5 border border-amber-700/30 rounded-lg p-2.5 mb-4">
          <p className="text-[10px] text-amber-500 uppercase tracking-wider mb-1.5 font-medium">Sentinel Recommendation</p>
          {a.recommendations.map((rec, i) => (
            <p key={i} className="text-xs text-amber-300/90 font-mono leading-relaxed">
              {rec}
            </p>
          ))}
        </div>
      )}

      {/* Apply button */}
      {a.recommendations.length > 0 && (
        <button
          onClick={() => onApply(a)}
          disabled={applying}
          className="w-full rounded-lg bg-amber-500/20 px-4 py-2.5 text-sm font-bold text-amber-400 border border-amber-500/40 transition-all hover:bg-amber-500/30 hover:text-amber-300 hover:border-amber-500/60 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {applying ? 'Sending...' : `Apply Sentinel Config for ${a.shortPair}`}
        </button>
      )}
    </div>
  );
}
