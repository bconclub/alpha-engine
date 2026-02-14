'use client';

import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';
import type { ExchangeFilter } from '@/lib/types';

const toggleOptions: { label: string; value: ExchangeFilter; activeBorder?: string }[] = [
  { label: 'All', value: 'all' },
  { label: 'Binance', value: 'binance', activeBorder: 'border-l-2 border-l-[#f0b90b]' },
  { label: 'Delta', value: 'delta', activeBorder: 'border-l-2 border-l-[#00d2ff]' },
];

export function ExchangeToggle() {
  const { exchangeFilter, setExchangeFilter } = useSupabase();

  return (
    <div className="flex flex-row items-center gap-1">
      {toggleOptions.map((option) => {
        const isActive = exchangeFilter === option.value;

        return (
          <button
            key={option.value}
            onClick={() => setExchangeFilter(option.value)}
            className={cn(
              'px-3 py-1.5 text-xs font-medium rounded-lg transition-colors',
              isActive
                ? cn('bg-zinc-700 text-white', option.activeBorder)
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
