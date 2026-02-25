'use client';

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import type { ChangelogEntry as ChangelogEntryType, Trade } from '@/lib/types';
import { changeTypeColor, getBeforeAfterSnapshots } from '@/lib/brain-utils';
import { BeforeAfterSnapshot } from './BeforeAfterSnapshot';

interface Props {
  entry: ChangelogEntryType;
  trades: Trade[];
  onAnalyze?: (entry: ChangelogEntryType) => void;
}

export function ChangelogEntryCard({ entry, trades, onAnalyze }: Props) {
  const [expanded, setExpanded] = useState(false);

  const snapshots = useMemo(() => {
    if (!entry.deployed_at || !expanded) return null;
    return getBeforeAfterSnapshots(trades, entry.deployed_at);
  }, [entry.deployed_at, trades, expanded]);

  const deployDate = entry.deployed_at
    ? new Date(entry.deployed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : null;

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-zinc-800/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Expand chevron */}
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
          className={cn('text-zinc-600 transition-transform flex-shrink-0', expanded ? 'rotate-90' : '')}>
          <path d="M3 2l4 3-4 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>

        {/* Type badge */}
        <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-mono uppercase', changeTypeColor(entry.change_type))}>
          {entry.change_type}
        </span>

        {/* Title */}
        <span className="text-sm text-white font-medium flex-1 truncate">{entry.title}</span>

        {/* Version */}
        {entry.version && (
          <span className="text-[10px] font-mono text-zinc-600">{entry.version}</span>
        )}

        {/* Status */}
        <span className={cn(
          'text-[10px] font-mono',
          entry.status === 'deployed' ? 'text-emerald-500' : entry.status === 'reverted' ? 'text-red-500' : 'text-amber-500',
        )}>
          {entry.status}
        </span>

        {/* Date */}
        {deployDate && (
          <span className="text-[10px] text-zinc-600">{deployDate}</span>
        )}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-zinc-800/50">
          {/* Description */}
          {entry.description && (
            <p className="text-xs text-zinc-400 mt-3 leading-relaxed">{entry.description}</p>
          )}

          {/* Parameters changed */}
          {(entry.parameters_before || entry.parameters_after) && (
            <div className="mt-3 grid grid-cols-2 gap-3">
              {entry.parameters_before && (
                <div>
                  <span className="text-[10px] text-zinc-600 uppercase tracking-wider">Before</span>
                  <pre className="mt-1 rounded bg-zinc-900 p-2 text-[10px] font-mono text-red-400/80 overflow-x-auto">
                    {JSON.stringify(entry.parameters_before, null, 2)}
                  </pre>
                </div>
              )}
              {entry.parameters_after && (
                <div>
                  <span className="text-[10px] text-zinc-600 uppercase tracking-wider">After</span>
                  <pre className="mt-1 rounded bg-zinc-900 p-2 text-[10px] font-mono text-emerald-400/80 overflow-x-auto">
                    {JSON.stringify(entry.parameters_after, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* Before/After snapshot */}
          {snapshots && <BeforeAfterSnapshot before={snapshots.before} after={snapshots.after} />}

          {!entry.deployed_at && (
            <p className="mt-3 text-[10px] text-zinc-600 italic">No deployed_at date — cannot compute impact snapshot</p>
          )}

          {/* Actions */}
          {onAnalyze && (
            <div className="mt-3 flex justify-end">
              <button
                onClick={(e) => { e.stopPropagation(); onAnalyze(entry); }}
                className="rounded-lg bg-purple-500/20 border border-purple-500/40 px-3 py-1.5 text-[10px] font-bold text-purple-400 hover:bg-purple-500/30 transition-colors"
              >
                Analyze Impact
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
