'use client';

import { useState, useEffect, useCallback } from 'react';
import { getSupabase } from '@/lib/supabase';
import { cn } from '@/lib/utils';
import type { AlphaAnalysis, ChangelogEntry, Trade } from '@/lib/types';
import { getBeforeAfterSnapshots } from '@/lib/brain-utils';
import { AnalysisCard } from './AnalysisCard';

interface Props {
  trades: Trade[];
  changelog: ChangelogEntry[];
  /** If set, auto-trigger analysis for this changelog entry */
  pendingAnalysis?: ChangelogEntry | null;
  onAnalysisDone?: () => void;
}

export function AnalysisTab({ trades, changelog, pendingAnalysis, onAnalysisDone }: Props) {
  const [analyses, setAnalyses] = useState<AlphaAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [latestResult, setLatestResult] = useState<any>(null);
  const [showHistory, setShowHistory] = useState(false);

  const fetchAnalyses = useCallback(async () => {
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data, error: err } = await sb
        .from('alpha_analysis')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(20);
      if (err) throw err;
      setAnalyses((data as AlphaAnalysis[]) || []);
    } catch (err) {
      console.error('Failed to fetch analyses:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAnalyses(); }, [fetchAnalyses]);

  // Auto-trigger when pendingAnalysis is set
  useEffect(() => {
    if (pendingAnalysis) {
      runAnalysis('changelog_impact', pendingAnalysis);
      onAnalysisDone?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAnalysis]);

  const runAnalysis = useCallback(async (type: string, entry?: ChangelogEntry) => {
    setAnalyzing(true);
    setError('');
    setLatestResult(null);

    try {
      const closedTrades = trades
        .filter(t => t.status === 'closed')
        .sort((a, b) => new Date(b.closed_at || b.timestamp).getTime() - new Date(a.closed_at || a.timestamp).getTime())
        .slice(0, 100);

      // Pre-compute breakdowns for richer analysis
      const byPair: Record<string, { wins: number; total: number; pnl: number }> = {};
      const byExit: Record<string, { wins: number; total: number; pnl: number }> = {};
      const byHour: Record<number, { wins: number; total: number }> = {};
      let totalGross = 0;
      let totalFees = 0;

      for (const t of closedTrades) {
        // By pair
        const pair = t.pair?.split('/')[0] || '?';
        if (!byPair[pair]) byPair[pair] = { wins: 0, total: 0, pnl: 0 };
        byPair[pair].total++;
        if (t.pnl >= 0) byPair[pair].wins++;
        byPair[pair].pnl += t.pnl;

        // By exit type
        const exit = t.exit_reason || t.reason || '?';
        if (!byExit[exit]) byExit[exit] = { wins: 0, total: 0, pnl: 0 };
        byExit[exit].total++;
        if (t.pnl >= 0) byExit[exit].wins++;
        byExit[exit].pnl += t.pnl;

        // By hour
        const hour = new Date(t.timestamp).getHours();
        if (!byHour[hour]) byHour[hour] = { wins: 0, total: 0 };
        byHour[hour].total++;
        if (t.pnl >= 0) byHour[hour].wins++;

        // Fee totals
        totalGross += (t.gross_pnl ?? t.pnl) || 0;
        totalFees += ((t.entry_fee ?? 0) + (t.exit_fee ?? 0));
      }

      const breakdowns = {
        by_pair: Object.fromEntries(
          Object.entries(byPair).map(([k, v]) => [k, {
            win_rate: `${((v.wins / v.total) * 100).toFixed(1)}%`,
            trades: v.total,
            pnl: `$${v.pnl.toFixed(4)}`,
          }]),
        ),
        by_exit: byExit,
        by_hour: byHour,
        fee_analysis: {
          total_gross_pnl: `$${totalGross.toFixed(4)}`,
          total_fees: `$${totalFees.toFixed(4)}`,
          total_net_pnl: `$${(totalGross - totalFees).toFixed(4)}`,
          fee_pct_of_gross: totalGross !== 0
            ? `${((totalFees / Math.abs(totalGross)) * 100).toFixed(1)}%`
            : 'N/A',
        },
      };

      // Get current params from latest param_change changelog entry
      const latestParamEntry = changelog.find(
        c => c.change_type === 'param_change' && c.parameters_after,
      );

      const body: any = {
        analysis_type: type,
        trades: closedTrades,
        changelog: changelog.slice(0, 10),
        breakdowns,
        current_params: latestParamEntry?.parameters_after ?? null,
      };

      if (entry) {
        body.changelog_entry_id = entry.id;
        body.changelog = [entry, ...changelog.filter(c => c.id !== entry.id).slice(0, 9)];
        if (entry.deployed_at) {
          body.snapshots = getBeforeAfterSnapshots(trades, entry.deployed_at);
        }
      }

      const res = await fetch('/api/brain/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setLatestResult(data.analysis);
      fetchAnalyses(); // Refresh history
    } catch (err: any) {
      setError(err.message || 'Analysis failed');
    } finally {
      setAnalyzing(false);
    }
  }, [trades, changelog, fetchAnalyses]);

  return (
    <div className="space-y-4">
      {/* Trigger buttons */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => runAnalysis('general')}
          disabled={analyzing}
          className={cn(
            'rounded-lg px-4 py-2.5 text-xs font-bold transition-all',
            analyzing
              ? 'bg-zinc-800 text-zinc-500 cursor-wait'
              : 'bg-purple-500/20 border border-purple-500/40 text-purple-400 hover:bg-purple-500/30',
          )}
        >
          {analyzing ? (
            <span className="flex items-center gap-2">
              <span className="inline-block w-3 h-3 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
              Analyzing...
            </span>
          ) : 'Analyze Overall'}
        </button>

        {changelog.length > 0 && (
          <button
            onClick={() => runAnalysis('changelog_impact', changelog[0])}
            disabled={analyzing}
            className={cn(
              'rounded-lg px-4 py-2.5 text-xs font-bold transition-all',
              analyzing
                ? 'bg-zinc-800 text-zinc-500 cursor-wait'
                : 'bg-blue-500/20 border border-blue-500/40 text-blue-400 hover:bg-blue-500/30',
            )}
          >
            Analyze Latest Change
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-400/10 border border-red-400/20 px-4 py-2.5 text-xs text-red-400">
          {error}
        </div>
      )}

      {/* Latest result */}
      {latestResult && (
        <div>
          <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Latest Analysis</h3>
          <AnalysisCard analysis={latestResult} />
        </div>
      )}

      {/* History */}
      {!loading && analyses.length > 0 && (
        <div>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="flex items-center gap-2 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            <span>Analysis History ({analyses.length})</span>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
              className={cn('transition-transform', showHistory ? 'rotate-180' : '')}>
              <path d="M3 4l2 2 2-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>

          {showHistory && (
            <div className="mt-3 space-y-3">
              {analyses.map(a => (
                <AnalysisCard key={a.id} analysis={a} />
              ))}
            </div>
          )}
        </div>
      )}

      {!loading && analyses.length === 0 && !latestResult && !analyzing && (
        <div className="text-center text-zinc-600 text-sm py-8">
          No analyses yet. Click &quot;Analyze Overall&quot; to get Claude&apos;s assessment.
        </div>
      )}
    </div>
  );
}
