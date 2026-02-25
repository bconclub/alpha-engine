'use client';

import { useState, useEffect, useCallback } from 'react';
import { getSupabase } from '@/lib/supabase';
import { cn } from '@/lib/utils';
import type { Trade, ChangelogEntry } from '@/lib/types';
import { ChangelogTab } from '@/components/brain/ChangelogTab';
import { AnalysisTab } from '@/components/brain/AnalysisTab';
import { SentinelTab } from '@/components/brain/SentinelTab';

// ─── Tab definitions ─────────────────────────────────────────────────────────

type Tab = 'changelog' | 'analysis' | 'sentinel';

const TABS: { key: Tab; label: string; description: string }[] = [
  { key: 'changelog', label: 'Changelog', description: 'Track GPFCs & changes' },
  { key: 'analysis',  label: 'Analysis',  description: 'Claude AI insights' },
  { key: 'sentinel',  label: 'Sentinel',  description: 'Pair analysis' },
];

// ─── Page ────────────────────────────────────────────────────────────────────

export default function BrainPage() {
  const [activeTab, setActiveTab] = useState<Tab>('changelog');
  const [trades, setTrades] = useState<Trade[]>([]);
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  // Cross-tab state: when user clicks "Analyze Impact" on a changelog entry
  const [pendingAnalysis, setPendingAnalysis] = useState<ChangelogEntry | null>(null);

  // ─── Data fetching (shared between Changelog & Analysis tabs) ──────────────

  const fetchTrades = useCallback(async () => {
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
        exit_reason: row.exit_reason ? String(row.exit_reason) : (row.reason ? String(row.reason) : undefined),
        order_id: row.order_id ? String(row.order_id) : undefined,
        setup_type: row.setup_type ? String(row.setup_type) : undefined,
      }));
      setTrades(normalized);
    } catch (err) {
      console.error('Failed to fetch trades:', err);
    }
  }, []);

  const fetchChangelog = useCallback(async () => {
    try {
      const sb = getSupabase();
      if (!sb) return;
      const { data, error } = await sb
        .from('changelog')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(100);
      if (error) throw error;
      setChangelog((data as ChangelogEntry[]) || []);
    } catch (err) {
      console.error('Failed to fetch changelog:', err);
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchTrades(), fetchChangelog()]);
      setLoading(false);
    };
    load();
  }, [fetchTrades, fetchChangelog]);

  // ─── Cross-tab: Changelog → Analysis ──────────────────────────────────────

  const handleAnalyzeEntry = useCallback((entry: ChangelogEntry) => {
    setPendingAnalysis(entry);
    setActiveTab('analysis');
  }, []);

  const handleAnalysisDone = useCallback(() => {
    setPendingAnalysis(null);
  }, []);

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Alpha Brain</h1>
          <p className="text-xs text-zinc-600 mt-0.5">Changelog, AI analysis, pair intelligence</p>
        </div>
        {loading && (
          <span className="inline-block w-4 h-4 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
        )}
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 bg-zinc-900/50 border border-zinc-800 rounded-xl p-1">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'flex-1 rounded-lg px-3 py-2 text-xs font-medium transition-all duration-150',
              activeTab === tab.key
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40',
            )}
          >
            <span className="block">{tab.label}</span>
            <span className={cn(
              'block text-[10px] mt-0.5 transition-colors',
              activeTab === tab.key ? 'text-zinc-400' : 'text-zinc-600',
            )}>
              {tab.description}
            </span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="min-h-[40vh]">
        {activeTab === 'changelog' && (
          <ChangelogTab
            trades={trades}
            onAnalyzeEntry={handleAnalyzeEntry}
          />
        )}

        {activeTab === 'analysis' && (
          <AnalysisTab
            trades={trades}
            changelog={changelog}
            pendingAnalysis={pendingAnalysis}
            onAnalysisDone={handleAnalysisDone}
          />
        )}

        {activeTab === 'sentinel' && (
          <SentinelTab />
        )}
      </div>
    </div>
  );
}
