'use client';

import { useState, useCallback } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { getSupabase } from '@/lib/supabase';
import { formatCurrency, formatPercentage, cn } from '@/lib/utils';
import type { Strategy } from '@/lib/types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STRATEGIES: Strategy[] = ['Grid', 'Momentum', 'Arbitrage'];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { botStatus } = useSupabase();

  // Force-strategy select state
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy>('Grid');

  // Loading & feedback state for each action
  const [pauseLoading, setPauseLoading] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [forceLoading, setForceLoading] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);

  // Confirmation state
  const [confirmAction, setConfirmAction] = useState<
    'pause' | 'force' | null
  >(null);

  // -- Helpers ---------------------------------------------------------------

  const showFeedback = useCallback(
    (type: 'success' | 'error', message: string) => {
      setFeedback({ type, message });
      setTimeout(() => setFeedback(null), 3000);
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
      showFeedback('success', 'Pause command sent successfully');
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
      showFeedback('success', 'Resume command sent successfully');
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to send resume command';
      showFeedback('error', message);
    } finally {
      setResumeLoading(false);
    }
  }, [showFeedback]);

  const handleForceStrategy = useCallback(async () => {
    if (confirmAction !== 'force') {
      setConfirmAction('force');
      return;
    }

    setConfirmAction(null);
    setForceLoading(true);
    try {
      const { error } = await getSupabase()!.from('bot_commands').insert({
        command: 'force_strategy',
        params: { strategy: selectedStrategy },
      });
      if (error) throw error;
      showFeedback(
        'success',
        `Force strategy "${selectedStrategy}" command sent`,
      );
    } catch (err: unknown) {
      const message =
        err instanceof Error
          ? err.message
          : 'Failed to send force strategy command';
      showFeedback('error', message);
    } finally {
      setForceLoading(false);
    }
  }, [confirmAction, selectedStrategy, showFeedback]);

  const cancelConfirm = useCallback(() => {
    setConfirmAction(null);
  }, []);

  // -------------------------------------------------------------------------
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight text-white">
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
      {/* Section 1: Bot Configuration (read-only)                          */}
      {/* ----------------------------------------------------------------- */}
      <div className="bg-card border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-6">
          Bot Configuration
        </h2>

        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-5">
          <div>
            <dt className="text-sm text-zinc-500">Trading Pair</dt>
            <dd className="mt-1 text-base font-medium text-zinc-100">
              BTC/USDT
            </dd>
          </div>
          <div>
            <dt className="text-sm text-zinc-500">Current Capital</dt>
            <dd className="mt-1 text-base font-medium font-mono text-zinc-100">
              {formatCurrency(botStatus?.capital ?? 0)}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-zinc-500">Active Strategy</dt>
            <dd className="mt-1 text-base font-medium text-zinc-100">
              {botStatus?.active_strategy ?? '--'}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-zinc-500">Win Rate</dt>
            <dd className="mt-1 text-base font-medium text-zinc-100">
              {botStatus ? formatPercentage(botStatus.win_rate) : '--'}
            </dd>
          </div>
        </dl>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Section 2: Manual Overrides                                       */}
      {/* ----------------------------------------------------------------- */}
      <div className="bg-card border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-6">
          Manual Controls
        </h2>

        <div className="space-y-6">
          {/* Pause / Resume row */}
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
                disabled={pauseLoading}
                className="rounded-lg bg-red-500/20 px-4 py-2 text-sm font-medium text-red-400 border border-red-500/30 transition-colors hover:bg-red-500/30 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {pauseLoading ? 'Sending...' : 'Pause Bot'}
              </button>
            )}

            {/* Resume button */}
            <button
              onClick={handleResume}
              disabled={resumeLoading}
              className="rounded-lg bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-400 border border-emerald-500/30 transition-colors hover:bg-emerald-500/30 hover:text-emerald-300 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {resumeLoading ? 'Sending...' : 'Resume Bot'}
            </button>
          </div>

          {/* Divider */}
          <hr className="border-zinc-800" />

          {/* Force strategy section */}
          <div>
            <h3 className="text-sm font-medium text-zinc-300 mb-3">
              Force Strategy
            </h3>

            <div className="flex flex-wrap items-center gap-3">
              <select
                value={selectedStrategy}
                onChange={(e) =>
                  setSelectedStrategy(e.target.value as Strategy)
                }
                className="h-10 rounded-lg border border-zinc-700 bg-zinc-800 px-3 text-sm text-zinc-200 outline-none focus:border-zinc-500"
              >
                {STRATEGIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>

              {confirmAction === 'force' ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-zinc-400">Are you sure?</span>
                  <button
                    onClick={handleForceStrategy}
                    disabled={forceLoading}
                    className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {forceLoading ? 'Sending...' : 'Confirm Force'}
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
                  onClick={handleForceStrategy}
                  disabled={forceLoading}
                  className="rounded-lg bg-amber-500/20 px-4 py-2 text-sm font-medium text-amber-400 border border-amber-500/30 transition-colors hover:bg-amber-500/30 hover:text-amber-300 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {forceLoading ? 'Sending...' : 'Force'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
