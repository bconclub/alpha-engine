import type { Trade, SnapshotStats } from './types';

/** Classify an exit_reason string into a standard bucket. */
export function classifyExit(reason: string | undefined): string {
  if (!reason) return 'OTHER';
  const r = reason.toUpperCase();
  if (r.includes('TRAIL')) return 'TRAIL';
  if (r.includes('STOP') || r.includes('SL') || r.includes('STOP-LOSS') || r.includes('STOP_LOSS')) return 'SL';
  if (r.includes('BREAKEVEN') || r.includes('BREAK-EVEN') || r.includes('BE EXIT')) return 'BE';
  if (r.includes('TIMEOUT') || r.includes('MAX HOLD') || r.includes('MAX_HOLD')) return 'TIMEOUT';
  if (r.includes('FLAT') || r.includes('FLATLINE')) return 'FLAT';
  if (r.includes('REVERSAL') || r.includes('SIGNAL')) return 'REV';
  if (r.includes('MANUAL') || r.includes('FORCE')) return 'MANUAL';
  if (r.includes('MOMENTUM_FADE') || r.includes('MOM_FADE')) return 'MOM_FADE';
  if (r.includes('DEAD_MOMENTUM') || r.includes('DEAD_MOM')) return 'DEAD_MOM';
  if (r.includes('TP') || r.includes('TAKE_PROFIT') || r.includes('HARD_TP')) return 'TP';
  if (r.includes('DECAY') || r.includes('EMERGENCY')) return 'DECAY';
  return 'OTHER';
}

/** Compute stats from an array of closed trades. */
export function computeSnapshot(trades: Trade[]): SnapshotStats {
  if (trades.length === 0) {
    return { trade_count: 0, win_rate: 0, avg_pnl: 0, total_pnl: 0, avg_hold_seconds: 0, exit_breakdown: {} };
  }

  const wins = trades.filter(t => t.pnl >= 0).length;
  const total_pnl = trades.reduce((s, t) => s + t.pnl, 0);

  // Hold time: closed_at - timestamp (opened_at)
  const holdTimes = trades
    .filter(t => t.closed_at && t.timestamp)
    .map(t => {
      const opened = new Date(t.timestamp).getTime();
      const closed = new Date(t.closed_at!).getTime();
      return Math.max(0, (closed - opened) / 1000);
    });

  const avg_hold = holdTimes.length > 0
    ? holdTimes.reduce((s, h) => s + h, 0) / holdTimes.length
    : 0;

  // Exit breakdown
  const exit_breakdown: Record<string, number> = {};
  for (const t of trades) {
    const cat = classifyExit(t.exit_reason);
    exit_breakdown[cat] = (exit_breakdown[cat] || 0) + 1;
  }

  return {
    trade_count: trades.length,
    win_rate: trades.length > 0 ? (wins / trades.length) * 100 : 0,
    avg_pnl: total_pnl / trades.length,
    total_pnl,
    avg_hold_seconds: Math.round(avg_hold),
    exit_breakdown,
  };
}

/**
 * Compute before/after snapshots for a changelog entry.
 * Before = last `windowSize` closed trades before `deployedAt`.
 * After = first `windowSize` closed trades on or after `deployedAt`.
 */
export function getBeforeAfterSnapshots(
  allTrades: Trade[],
  deployedAt: string,
  windowSize: number = 50,
): { before: SnapshotStats; after: SnapshotStats } {
  const deployTs = new Date(deployedAt).getTime();
  const closed = allTrades.filter(t => t.status === 'closed' && t.closed_at);

  const before = closed
    .filter(t => new Date(t.closed_at!).getTime() < deployTs)
    .sort((a, b) => new Date(b.closed_at!).getTime() - new Date(a.closed_at!).getTime())
    .slice(0, windowSize);

  const after = closed
    .filter(t => new Date(t.closed_at!).getTime() >= deployTs)
    .sort((a, b) => new Date(a.closed_at!).getTime() - new Date(b.closed_at!).getTime())
    .slice(0, windowSize);

  return {
    before: computeSnapshot(before),
    after: computeSnapshot(after),
  };
}

/** Format seconds into human-readable (e.g. "2m 30s"). */
export function formatHold(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

/** Change type badge color. */
export function changeTypeColor(type: string): string {
  switch (type) {
    case 'gpfc': return 'bg-blue-500/20 text-blue-400';
    case 'bugfix': return 'bg-red-500/20 text-red-400';
    case 'param_change': return 'bg-amber-500/20 text-amber-400';
    case 'feature': return 'bg-emerald-500/20 text-emerald-400';
    case 'revert': return 'bg-purple-500/20 text-purple-400';
    case 'strategy': return 'bg-cyan-500/20 text-cyan-400';
    default: return 'bg-zinc-500/20 text-zinc-400';
  }
}
