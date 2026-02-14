'use client';

import { useRef, useEffect, useState, useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatShortDate, cn } from '@/lib/utils';
import type { ActivityEventType, ActivityFilter } from '@/lib/types';

const EVENT_ICONS: Record<ActivityEventType, string> = {
  analysis: '\u{1F50D}',
  strategy_switch: '\u{1F500}',
  trade_open: '\u{1F7E2}',
  trade_close: '\u2705',
  short_open: '\u{1F534}',
  risk_alert: '\u26A0\uFE0F',
};

const EVENT_COLORS: Record<ActivityEventType, string> = {
  analysis: 'border-l-[#2196f3]',
  strategy_switch: 'border-l-[#ffd600]',
  trade_open: 'border-l-[#00c853]',
  trade_close: 'border-l-[#00c853]',
  short_open: 'border-l-[#ff1744]',
  risk_alert: 'border-l-[#ffd600]',
};

const FILTER_OPTIONS: { value: ActivityFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'trades', label: 'Trades Only' },
  { value: 'alerts', label: 'Alerts Only' },
];

export function LiveActivityFeed() {
  const { activityFeed } = useSupabase();
  const [filter, setFilter] = useState<ActivityFilter>('all');
  const scrollRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    if (filter === 'all') return activityFeed;
    if (filter === 'trades') {
      return activityFeed.filter((e) =>
        e.eventType === 'trade_open' ||
        e.eventType === 'trade_close' ||
        e.eventType === 'short_open',
      );
    }
    return activityFeed.filter((e) => e.eventType === 'risk_alert');
  }, [activityFeed, filter]);

  // Auto-scroll when new items arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [filtered.length]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Live Activity
        </h3>
        <div className="flex gap-1">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setFilter(opt.value)}
              className={cn(
                'px-2.5 py-1 rounded text-[10px] font-medium transition-colors',
                filter === opt.value
                  ? 'bg-zinc-700 text-white'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50',
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div ref={scrollRef} className="max-h-64 overflow-y-auto space-y-1 pr-1">
        {filtered.length === 0 ? (
          <p className="text-sm text-zinc-500 text-center py-6">No activity yet</p>
        ) : (
          filtered.map((event) => (
            <div
              key={`${event.id}-${event.eventType}`}
              className={cn(
                'flex items-start gap-3 px-3 py-2 rounded-md border-l-2 bg-zinc-900/30',
                EVENT_COLORS[event.eventType],
              )}
            >
              <span className="text-sm mt-0.5 flex-shrink-0">
                {EVENT_ICONS[event.eventType]}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono text-zinc-500 flex-shrink-0">
                    {formatShortDate(event.timestamp)}
                  </span>
                  {event.pair && (
                    <span className="text-[10px] font-medium text-zinc-400">{event.pair}</span>
                  )}
                </div>
                <p className="text-xs text-zinc-300 mt-0.5 truncate">{event.description}</p>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
