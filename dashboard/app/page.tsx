'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { LiveStatusBar } from '@/components/dashboard/LiveStatusBar';
import { MarketOverview } from '@/components/dashboard/MarketOverview';
import { TriggerProximity } from '@/components/dashboard/TriggerProximity';
import { LiveActivityFeed } from '@/components/dashboard/LiveActivityFeed';
import { OpenPositions } from '@/components/dashboard/OpenPositions';
import { PerformancePanel } from '@/components/dashboard/PerformancePanel';

function ConnectionBanner() {
  const { isConnected, trades, strategyLog } = useSupabase();

  return (
    <div className="bg-zinc-900/60 border border-zinc-800 rounded-lg px-4 py-2 flex items-center gap-3 text-xs">
      <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-[#00c853] animate-pulse' : 'bg-red-500'}`} />
      <span className="text-zinc-400">
        {isConnected ? 'Realtime connected' : 'Realtime disconnected'}
      </span>
      <span className="text-zinc-600">|</span>
      <span className="text-zinc-400">
        {trades.length} trades loaded
      </span>
      <span className="text-zinc-600">|</span>
      <span className="text-zinc-400">
        {strategyLog.length} strategy logs
      </span>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-4">
      {/* Connection status */}
      <ConnectionBanner />

      {/* 1. Live Status Bar — full width */}
      <LiveStatusBar />

      {/* 2 & 3. Market Overview (60%) + Trigger Proximity (40%) */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <MarketOverview />
        </div>
        <div className="lg:col-span-2">
          <TriggerProximity />
        </div>
      </div>

      {/* 4. Live Activity Feed — full width */}
      <LiveActivityFeed />

      {/* 5. Open Positions */}
      <OpenPositions />

      {/* 6. Performance — full width, collapsible */}
      <PerformancePanel />
    </div>
  );
}
