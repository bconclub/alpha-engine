'use client';

import { useState, useMemo, useCallback } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { getSupabase } from '@/lib/supabase';
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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

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

function DiagRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-[10px] text-zinc-500">{label}</span>
      <span className="text-[11px] font-mono text-zinc-300">{children}</span>
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

  // -- Derived state --
  const isPaused = useMemo(() => {
    return botStatus?.is_paused === true || botStatus?.bot_state === 'paused';
  }, [botStatus]);

  const pauseReason = useMemo(() => {
    return botStatus?.pause_reason ?? null;
  }, [botStatus]);

  const scalpEnabled = botStatus?.scalp_enabled ?? true;
  const optionsEnabled = botStatus?.options_scalp_enabled ?? false;

  const pairEntries = useMemo(() => {
    if (!diag?.pairs) return [];
    return Object.entries(diag.pairs).sort(([a], [b]) => a.localeCompare(b));
  }, [diag?.pairs]);

  // -- UI state --
  const [showWhyNoTrades, setShowWhyNoTrades] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);
  const [confirmAction, setConfirmAction] = useState<'pause' | 'force_resume' | null>(null);

  // -- Helpers --
  const showFeedback = useCallback((type: 'success' | 'error', message: string) => {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 4000);
  }, []);

  const cancelConfirm = useCallback(() => setConfirmAction(null), []);

  // -- Command handlers --
  const handlePause = useCallback(async () => {
    if (confirmAction !== 'pause') { setConfirmAction('pause'); return; }
    setConfirmAction(null);
    setPauseLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({ command: 'pause', params: {} });
      if (error) throw error;
      showFeedback('success', 'Pause command sent');
    } catch (err: unknown) {
      showFeedback('error', err instanceof Error ? err.message : 'Failed to send pause command');
    } finally { setPauseLoading(false); }
  }, [confirmAction, showFeedback]);

  const handleResume = useCallback(async () => {
    setResumeLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({ command: 'resume', params: {} });
      if (error) throw error;
      showFeedback('success', 'Resume command sent');
    } catch (err: unknown) {
      showFeedback('error', err instanceof Error ? err.message : 'Failed to send resume command');
    } finally { setResumeLoading(false); }
  }, [showFeedback]);

  const handleForceResume = useCallback(async () => {
    if (confirmAction !== 'force_resume') { setConfirmAction('force_resume'); return; }
    setConfirmAction(null);
    setResumeLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({ command: 'resume', params: { force: true } });
      if (error) throw error;
      showFeedback('success', 'Force resume sent — win-rate bypass active');
    } catch (err: unknown) {
      showFeedback('error', err instanceof Error ? err.message : 'Failed to send force resume');
    } finally { setResumeLoading(false); }
  }, [confirmAction, showFeedback]);

  const handleStrategyToggle = useCallback(async (strategy: 'scalp' | 'options_scalp') => {
    const client = getSupabase();
    if (!client) return;
    const currentlyEnabled = strategy === 'scalp' ? scalpEnabled : optionsEnabled;
    try {
      await client.from('bot_commands').insert({
        command: 'toggle_strategy',
        params: { strategy, enabled: !currentlyEnabled },
        executed: false,
      });
      showFeedback('success', `${strategy === 'scalp' ? 'Scalp' : 'Options'} ${currentlyEnabled ? 'disabled' : 'enabled'}`);
    } catch {
      showFeedback('error', 'Failed to toggle strategy');
    }
  }, [scalpEnabled, optionsEnabled, showFeedback]);

  // -- "Why No Trades?" lines --
  const pairLines = useMemo(() => {
    if (!diag?.pairs) return [];
    return Object.entries(diag.pairs)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([pair, d]: [string, any]) => {
        const base = extractBase(pair);
        if (d.in_position) return { base, text: `IN ${(d.position_side ?? 'TRADE').toUpperCase()}`, color: 'text-amber-400' };
        if (d.skip_reason === 'NONE') return { base, text: 'READY', color: 'text-[#00c853]' };
        return { base, text: d.skip_reason, color: 'text-zinc-400' };
      });
  }, [diag?.pairs]);

  // ---------------------------------------------------------------------------
  // No data state
  // ---------------------------------------------------------------------------
  if (!botStatus) {
    return (
      <div className="p-4 md:p-6">
        <h1 className="text-lg font-medium text-white mb-4">Status</h1>
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
        <h1 className="text-lg font-medium text-white">Status</h1>
        <span className="text-[10px] font-mono text-zinc-600">
          Updated {timeSince(lastUpdated)}
        </span>
      </div>

      {/* Feedback toast */}
      {feedback && (
        <div className={cn(
          'rounded-lg px-4 py-3 text-sm font-medium',
          feedback.type === 'success'
            ? 'bg-emerald-400/10 text-emerald-400 border border-emerald-400/20'
            : 'bg-red-400/10 text-red-400 border border-red-400/20',
        )}>
          {feedback.message}
        </div>
      )}

      {/* ═══ A. PULSE BAR ═══ */}
      {pairEntries.length > 0 && (
        <div className="flex items-center gap-4 bg-[#0d1117] border border-zinc-800 rounded-xl px-4 py-3">
          {pairEntries.map(([pair, d]: [string, any]) => {
            const hasAnyCooldown = d.cooldowns.sl > 0 || d.cooldowns.reversal > 0
              || d.cooldowns.streak > 0 || d.cooldowns.phantom > 0;
            const color = isPaused ? 'bg-[#ff1744]'
              : d.in_position ? 'bg-amber-400'
              : hasAnyCooldown ? 'bg-amber-400'
              : d.skip_reason !== 'NONE' ? 'bg-zinc-500'
              : 'bg-[#00c853]';
            const shouldPulse = !isPaused && !hasAnyCooldown && d.skip_reason === 'NONE' && !d.in_position;
            return (
              <div key={pair} className="flex items-center gap-1.5">
                <span className={cn('w-3 h-3 rounded-full', color, shouldPulse && 'animate-pulse')} />
                <span className="text-xs font-medium text-zinc-300">{extractBase(pair)}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* ═══ B. PAUSE / RESUME CONTROLS ═══ */}
      <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
        {isPaused ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-[#ff1744] animate-pulse" />
              <span className="text-sm font-semibold text-[#ff1744]">BOT PAUSED</span>
            </div>
            {pauseReason && (
              <p className="text-xs font-mono text-zinc-400">{pauseReason}</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {/* Normal Resume */}
              <button
                onClick={handleResume}
                disabled={resumeLoading}
                className="rounded-lg bg-emerald-500 px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {resumeLoading ? 'Sending...' : 'Resume Bot'}
              </button>

              {/* Force Resume */}
              {confirmAction === 'force_resume' ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400">Override safety check?</span>
                  <button
                    onClick={handleForceResume}
                    disabled={resumeLoading}
                    className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-600 disabled:opacity-50"
                  >
                    Confirm
                  </button>
                  <button onClick={cancelConfirm} className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700">
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={handleForceResume}
                  disabled={resumeLoading}
                  className="rounded-lg bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-400 border border-emerald-500/30 transition-colors hover:bg-emerald-500/30 disabled:opacity-50"
                >
                  Force Resume
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-[#00c853] animate-pulse" />
              <span className="text-sm font-semibold text-[#00c853]">BOT RUNNING</span>
            </div>
            {confirmAction === 'pause' ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400">Are you sure?</span>
                <button
                  onClick={handlePause}
                  disabled={pauseLoading}
                  className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-50"
                >
                  {pauseLoading ? 'Sending...' : 'Confirm Pause'}
                </button>
                <button onClick={cancelConfirm} className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700">
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={handlePause}
                disabled={pauseLoading}
                className="rounded-lg bg-red-500/20 px-5 py-2.5 text-sm font-semibold text-red-400 border border-red-500/30 transition-colors hover:bg-red-500/30 hover:text-red-300 disabled:opacity-50"
              >
                Pause Bot
              </button>
            )}
          </div>
        )}
      </div>

      {/* ═══ C. WHY NO TRADES? ═══ */}
      <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
        <button
          onClick={() => setShowWhyNoTrades(v => !v)}
          className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-zinc-300 hover:bg-zinc-800/40 transition-colors"
        >
          <span>Why No Trades?</span>
          <svg
            width="16" height="16" viewBox="0 0 16 16" fill="none"
            className={cn('transition-transform duration-200', showWhyNoTrades && 'rotate-180')}
          >
            <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        {showWhyNoTrades && (
          <div className="px-4 pb-4 space-y-1.5 border-t border-zinc-800/50 pt-3">
            {pairLines.length === 0 ? (
              <p className="text-xs text-zinc-500">No diagnostics data — update engine to v3.23+</p>
            ) : (
              pairLines.map((line, i) => (
                <p key={i} className="text-xs font-mono">
                  <span className="text-zinc-500 inline-block w-10">{line.base}:</span>{' '}
                  <span className={line.color}>{line.text}</span>
                </p>
              ))
            )}
          </div>
        )}
      </div>

      {/* ═══ D. DIAGNOSTICS ═══ */}
      <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
        <h2 className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-3">Diagnostics</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2">
          <DiagRow label="State">
            <span className={cn('font-medium', botStatus.bot_state === 'running' ? 'text-[#00c853]' : 'text-[#ff1744]')}>
              {(botStatus.bot_state ?? 'unknown').toUpperCase()}
            </span>
          </DiagRow>
          <DiagRow label="Delta Balance">
            <span>${diag?.balance?.delta?.toFixed(2) ?? '—'}</span>
            {' '}
            <span className={cn('text-[9px]', diag?.balance?.delta_min_trade ? 'text-[#00c853]' : 'text-[#ff1744]')}>
              {diag?.balance?.delta_min_trade ? '✓' : '✗'}
            </span>
          </DiagRow>
          <DiagRow label="Binance Balance">
            <span>${diag?.balance?.binance?.toFixed(2) ?? '—'}</span>
            {' '}
            <span className={cn('text-[9px]', diag?.balance?.binance_min_trade ? 'text-[#00c853]' : 'text-[#ff1744]')}>
              {diag?.balance?.binance_min_trade ? '✓' : '✗'}
            </span>
          </DiagRow>
          <DiagRow label="Positions">
            {diag?.positions?.open ?? '?'} / {diag?.positions?.max ?? '?'}
          </DiagRow>
          <DiagRow label="Uptime">
            {formatUptime(botStatus.uptime_seconds)}
          </DiagRow>
          <DiagRow label="Last Scan">
            {diag ? `${diag.last_scan_ago_s}s ago` : '—'}
          </DiagRow>
          <DiagRow label="Market Regime">
            <Badge color={
              botStatus.market_regime === 'CHOPPY' ? 'red'
              : botStatus.market_regime?.startsWith('TRENDING') ? 'green'
              : 'zinc'
            }>
              {botStatus.market_regime ?? '—'}
            </Badge>
          </DiagRow>
        </div>
        {/* Open position pairs */}
        {diag?.positions?.pairs && diag.positions.pairs.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-3 pt-3 border-t border-zinc-800/50">
            <span className="text-[10px] text-zinc-500 mr-1">Open:</span>
            {diag.positions.pairs.map((p: string) => (
              <Badge key={p} color="amber">{extractBase(p)}</Badge>
            ))}
          </div>
        )}
      </div>

      {/* ═══ E. STRATEGY TOGGLES ═══ */}
      <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-4">
        <h2 className="text-xs font-medium text-zinc-400 uppercase tracking-wider mb-3">Strategies</h2>
        <div className="flex flex-wrap gap-3">
          {([
            { key: 'scalp' as const, label: 'Scalp', enabled: scalpEnabled },
            { key: 'options_scalp' as const, label: 'Options Scalp', enabled: optionsEnabled },
          ]).map(({ key, label, enabled }) => (
            <div
              key={key}
              className={cn(
                'flex items-center gap-3 border rounded-xl px-4 py-3 transition-all',
                enabled ? 'border-zinc-700 bg-zinc-900/50' : 'border-zinc-800 bg-zinc-900/30 opacity-60',
              )}
            >
              <span className="text-sm font-semibold text-white">{label}</span>
              <button
                onClick={() => handleStrategyToggle(key)}
                className={cn(
                  'relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 shrink-0',
                  enabled ? 'bg-emerald-500' : 'bg-zinc-700',
                )}
              >
                <span className={cn(
                  'inline-block h-3 w-3 rounded-full bg-white transition-transform duration-200',
                  enabled ? 'translate-x-5' : 'translate-x-1',
                )} />
              </button>
              <span className={cn('text-[10px] font-mono', enabled ? 'text-emerald-400' : 'text-zinc-500')}>
                {enabled ? 'ON' : 'OFF'}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ═══ F. PER-PAIR DIAGNOSTICS ═══ */}
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
            {pairEntries.map(([pair, d]: [string, any]) => {
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
