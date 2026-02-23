'use client';

import { useState, useCallback, useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { getSupabase } from '@/lib/supabase';
import { formatCurrency, formatPercentage, cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { botStatus } = useSupabase();

  // Loading & feedback state
  const [pauseLoading, setPauseLoading] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);

  // Confirmation state
  const [confirmAction, setConfirmAction] = useState<
    'pause' | 'force_resume' | null
  >(null);

  // Derived bot state
  const isPaused = useMemo(() => {
    return botStatus?.is_paused === true || botStatus?.bot_state === 'paused';
  }, [botStatus]);

  const pauseReason = useMemo(() => {
    return botStatus?.pause_reason ?? null;
  }, [botStatus]);

  // -- Helpers ---------------------------------------------------------------

  const showFeedback = useCallback(
    (type: 'success' | 'error', message: string) => {
      setFeedback({ type, message });
      setTimeout(() => setFeedback(null), 4000);
    },
    [],
  );

  // -- Command handlers ------------------------------------------------------

  const handlePause = useCallback(async () => {
    if (confirmAction !== 'pause') {
      setConfirmAction('pause');
      return;
    }

    setConfirmAction(null);
    setPauseLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({
        command: 'pause',
        params: {},
      });
      if (error) throw error;
      showFeedback('success', 'Pause command sent');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to send pause command';
      showFeedback('error', message);
    } finally {
      setPauseLoading(false);
    }
  }, [confirmAction, showFeedback]);

  const handleResume = useCallback(async () => {
    setResumeLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({
        command: 'resume',
        params: {},
      });
      if (error) throw error;
      showFeedback('success', 'Resume command sent');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to send resume command';
      showFeedback('error', message);
    } finally {
      setResumeLoading(false);
    }
  }, [showFeedback]);

  const handleForceResume = useCallback(async () => {
    if (confirmAction !== 'force_resume') {
      setConfirmAction('force_resume');
      return;
    }

    setConfirmAction(null);
    setResumeLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({
        command: 'resume',
        params: { force: true },
      });
      if (error) throw error;
      showFeedback('success', 'Force resume sent — win-rate bypass active');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to send force resume';
      showFeedback('error', message);
    } finally {
      setResumeLoading(false);
    }
  }, [confirmAction, showFeedback]);

  const cancelConfirm = useCallback(() => {
    setConfirmAction(null);
  }, []);

  // -------------------------------------------------------------------------
  return (
    <div className="space-y-6">
      <h1 className="text-xl md:text-2xl font-bold tracking-tight text-white">
        Settings
      </h1>

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

      {/* ----------------------------------------------------------------- */}
      {/* Paused Banner — shown when bot is paused                          */}
      {/* ----------------------------------------------------------------- */}
      {isPaused && (
        <div className="bg-[#ff1744]/5 border border-[#ff1744]/20 rounded-xl p-4 md:p-5">
          <div className="flex items-start gap-3">
            <div className="w-2.5 h-2.5 rounded-full bg-[#ff1744] mt-1 shrink-0 animate-pulse" />
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-[#ff1744] mb-1">Bot Paused</h3>
              {pauseReason && (
                <p className="text-xs text-zinc-400 font-mono mb-3">{pauseReason}</p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                {/* Force Resume — bypasses win-rate check */}
                {confirmAction === 'force_resume' ? (
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400">Override safety check?</span>
                    <button
                      onClick={handleForceResume}
                      disabled={resumeLoading}
                      className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {resumeLoading ? 'Sending...' : 'Confirm Force Resume'}
                    </button>
                    <button
                      onClick={cancelConfirm}
                      className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={handleForceResume}
                    disabled={resumeLoading}
                    className="rounded-lg bg-emerald-500/20 px-4 py-2 text-sm font-semibold text-emerald-400 border border-emerald-500/30 transition-colors hover:bg-emerald-500/30 hover:text-emerald-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {resumeLoading ? 'Sending...' : 'Force Resume'}
                  </button>
                )}
                <span className="text-[10px] text-zinc-600 font-mono">
                  Bypasses win-rate check until next winning trade
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Section 1: Bot Status                                             */}
      {/* ----------------------------------------------------------------- */}
      <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4 md:mb-6">
          Bot Status
        </h2>

        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 md:gap-x-8 gap-y-4 md:gap-y-5">
          <div>
            <dt className="text-xs text-zinc-500">State</dt>
            <dd className="mt-1 text-sm font-semibold">
              {isPaused ? (
                <span className="text-[#ff1744]">PAUSED</span>
              ) : (
                <span className="text-[#00c853]">RUNNING</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Capital</dt>
            <dd className="mt-1 text-sm font-medium font-mono text-zinc-100">
              {formatCurrency(botStatus?.capital ?? 0)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Win Rate</dt>
            <dd className={cn(
              'mt-1 text-sm font-medium font-mono',
              (botStatus?.win_rate ?? 0) < 40 ? 'text-[#ff1744]' : 'text-zinc-100',
            )}>
              {botStatus ? formatPercentage(botStatus.win_rate) : '--'}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Daily P&L</dt>
            <dd className={cn(
              'mt-1 text-sm font-medium font-mono',
              (botStatus?.daily_pnl ?? 0) >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]',
            )}>
              {botStatus?.daily_pnl != null
                ? `${botStatus.daily_pnl >= 0 ? '+' : ''}${formatCurrency(botStatus.daily_pnl)}`
                : '--'}
            </dd>
          </div>
        </dl>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Section 2: Manual Controls                                        */}
      {/* ----------------------------------------------------------------- */}
      <div className="bg-card border border-zinc-800 rounded-xl p-4 md:p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-6">
          Manual Controls
        </h2>

        <div className="flex flex-wrap items-center gap-3">
          {/* Pause button */}
          {confirmAction === 'pause' ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-zinc-400">Are you sure?</span>
              <button
                onClick={handlePause}
                disabled={pauseLoading}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {pauseLoading ? 'Sending...' : 'Confirm Pause'}
              </button>
              <button
                onClick={cancelConfirm}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={handlePause}
              disabled={pauseLoading || isPaused}
              className="rounded-lg bg-red-500/20 px-4 py-2 text-sm font-medium text-red-400 border border-red-500/30 transition-colors hover:bg-red-500/30 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {pauseLoading ? 'Sending...' : 'Pause Bot'}
            </button>
          )}

          {/* Resume button (normal — no force) */}
          <button
            onClick={handleResume}
            disabled={resumeLoading || !isPaused}
            className="rounded-lg bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-400 border border-emerald-500/30 transition-colors hover:bg-emerald-500/30 hover:text-emerald-300 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {resumeLoading ? 'Sending...' : 'Resume Bot'}
          </button>
        </div>
      </div>
    </div>
  );
}
