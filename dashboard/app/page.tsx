'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { LiveStatusBar } from '@/components/dashboard/LiveStatusBar';
import { MarketOverview } from '@/components/dashboard/MarketOverview';
import { TriggerProximity } from '@/components/dashboard/TriggerProximity';
import { LiveActivityFeed } from '@/components/dashboard/LiveActivityFeed';
import { LivePositions } from '@/components/dashboard/LivePositions';
import { OpenPositions } from '@/components/dashboard/OpenPositions';
import { PerformancePanel } from '@/components/dashboard/PerformancePanel';

function ConnectionBanner() {
  const { isConnected, trades, strategyLog, botStatus } = useSupabase();

  const shortingEnabled = botStatus?.shorting_enabled ?? false;
  const leverageLevel = botStatus?.leverage ?? botStatus?.leverage_level ?? 1;
  const activeStrategiesCount = botStatus?.active_strategy_count ?? botStatus?.active_strategies_count ?? 0;

  return (
    <div className="bg-zinc-900/60 border border-zinc-800 rounded-lg px-3 md:px-4 py-2 flex flex-wrap items-center gap-2 md:gap-3 text-xs">
      <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-[#00c853] animate-pulse' : 'bg-red-500'}`} />
      <span className="text-zinc-400">
        {isConnected ? 'Connected' : 'Disconnected'}
      </span>
      {/* Mobile: show key indicators inline */}
      <span className="text-zinc-700 md:hidden">|</span>
      <span className={`md:hidden ${shortingEnabled ? 'text-[#00c853]' : 'text-zinc-600'}`}>
        Short {shortingEnabled ? 'ON' : 'OFF'}
      </span>
      <span className="text-zinc-700 md:hidden">|</span>
      <span className="text-[#ffd600] font-mono md:hidden">{leverageLevel}x</span>
      <span className="text-zinc-700 md:hidden">|</span>
      <span className="text-[#2196f3] font-mono md:hidden">{activeStrategiesCount} strats</span>
      {/* Desktop: show trades & logs */}
      <span className="text-zinc-600 hidden md:inline">|</span>
      <span className="text-zinc-400 hidden md:inline">
        {trades.length} trades
      </span>
      <span className="text-zinc-600 hidden md:inline">|</span>
      <span className="text-zinc-400 hidden md:inline">
        {strategyLog.length} logs
      </span>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-3 md:space-y-4">
      {/* Connection status */}
      <ConnectionBanner />

      {/* 1. Live Status Bar — full width */}
      <LiveStatusBar />

      {/* 2 & 3. Market Overview (60%) + Trigger Proximity (40%) */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3 space-y-2">
          <MarketOverview />
          {/* Live Positions — docked under market overview, no gap */}
          <LivePositions />
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
