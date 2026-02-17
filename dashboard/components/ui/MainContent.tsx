'use client';

import type { ReactNode } from 'react';
import { useSidebar } from '@/components/providers/SidebarProvider';
import { cn } from '@/lib/utils';

export function MainContent({ children }: { children: ReactNode }) {
  const { collapsed } = useSidebar();

  return (
    <main
      className={cn(
        'min-h-screen pt-14 md:pt-0 pb-16 md:pb-0 w-auto transition-all duration-200',
        collapsed ? 'ml-0 md:ml-14' : 'ml-0 md:ml-56',
      )}
    >
      <div className="max-w-[1920px] mx-auto px-3 py-3 md:px-5 md:py-4">
        {children}
      </div>
    </main>
  );
}
