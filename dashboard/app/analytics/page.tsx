'use client';

import { AnalyticsPanel } from '@/components/dashboard/AnalyticsPanel';

export default function AnalyticsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight text-white">
        Analytics
      </h1>
      <AnalyticsPanel />
    </div>
  );
}
