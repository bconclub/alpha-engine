'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatTimeAgo, cn } from '@/lib/utils';

export function BotStatusIndicator() {
  const { botStatus, isConnected } = useSupabase();

  const isOnline = useMemo(() => {
    if (!isConnected || !botStatus?.timestamp) return false;

    const lastHeartbeat = new Date(botStatus.timestamp).getTime();
    const now = Date.now();
    return now - lastHeartbeat < 120_000; // 120 seconds
  }, [isConnected, botStatus]);

  return (
    <div className="flex items-center gap-2 text-sm">
      {/* Status dot */}
      <span
        className={cn(
          'w-2 h-2 rounded-full',
          isOnline
            ? 'bg-emerald-400 animate-pulse'
            : 'bg-red-400'
        )}
      />

      {/* Status text */}
      <span className={isOnline ? 'text-emerald-400' : 'text-red-400'}>
        {isOnline ? 'Online' : 'Offline'}
      </span>

      {/* Last heartbeat */}
      {botStatus?.timestamp && (
        <span className="text-zinc-500 text-xs">
          &middot; {formatTimeAgo(botStatus.timestamp)}
        </span>
      )}

      {/* Uptime placeholder */}
      <span className="text-zinc-600 text-xs hidden sm:inline">
        &middot; Uptime: --
      </span>
    </div>
  );
}
