'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';

const navItems = [
  {
    name: 'Overview',
    href: '/',
    icon: (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M2 3C2 2.44772 2.44772 2 3 2H8C8.55228 2 9 2.44772 9 3V10C9 10.5523 8.55228 11 8 11H3C2.44772 11 2 10.5523 2 10V3Z"
          fill="currentColor"
        />
        <path
          d="M11 3C11 2.44772 11.4477 2 12 2H17C17.5523 2 18 2.44772 18 3V6C18 6.55228 17.5523 7 17 7H12C11.4477 7 11 6.55228 11 6V3Z"
          fill="currentColor"
        />
        <path
          d="M11 10C11 9.44772 11.4477 9 12 9H17C17.5523 9 18 9.44772 18 10V17C18 17.5523 17.5523 18 17 18H12C11.4477 18 11 17.5523 11 17V10Z"
          fill="currentColor"
        />
        <path
          d="M2 14C2 13.4477 2.44772 13 3 13H8C8.55228 13 9 13.4477 9 14V17C9 17.5523 8.55228 18 8 18H3C2.44772 18 2 17.5523 2 17V14Z"
          fill="currentColor"
        />
      </svg>
    ),
  },
  {
    name: 'Trades',
    href: '/trades',
    icon: (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M3 4C3 3.44772 3.44772 3 4 3H16C16.5523 3 17 3.44772 17 4C17 4.55228 16.5523 5 16 5H4C3.44772 5 3 4.55228 3 4Z" fill="currentColor" />
        <path d="M3 8C3 7.44772 3.44772 7 4 7H16C16.5523 7 17 7.44772 17 8C17 8.55228 16.5523 9 16 9H4C3.44772 9 3 8.55228 3 8Z" fill="currentColor" />
        <path d="M4 11C3.44772 11 3 11.4477 3 12C3 12.5523 3.44772 13 4 13H16C16.5523 13 17 12.5523 17 12C17 11.4477 16.5523 11 16 11H4Z" fill="currentColor" />
        <path d="M3 16C3 15.4477 3.44772 15 4 15H16C16.5523 15 17 15.4477 17 16C17 16.5523 16.5523 17 16 17H4C3.44772 17 3 16.5523 3 16Z" fill="currentColor" />
      </svg>
    ),
  },
  {
    name: 'Strategies',
    href: '/strategies',
    icon: (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path fillRule="evenodd" clipRule="evenodd" d="M5 2C3.34315 2 2 3.34315 2 5V7C2 8.65685 3.34315 10 5 10H7C8.65685 10 10 8.65685 10 7V5C10 3.34315 8.65685 2 7 2H5ZM5 4C4.44772 4 4 4.44772 4 5V7C4 7.55228 4.44772 8 5 8H7C7.55228 8 8 7.55228 8 7V5C8 4.44772 7.55228 4 7 4H5Z" fill="currentColor" />
        <path d="M12 5C12 4.44772 12.4477 4 13 4H17C17.5523 4 18 4.44772 18 5C18 5.55228 17.5523 6 17 6H13C12.4477 6 12 5.55228 12 5Z" fill="currentColor" />
        <path d="M12 7C12 6.44772 12.4477 6 13 6H15C15.5523 6 16 6.44772 16 7C16 7.55228 15.5523 8 15 8H13C12.4477 8 12 7.55228 12 7Z" fill="currentColor" />
        <path d="M12 13C12 12.4477 12.4477 12 13 12H17C17.5523 12 18 12.4477 18 13C18 13.5523 17.5523 14 17 14H13C12.4477 14 12 13.5523 12 13Z" fill="currentColor" />
        <path d="M12 15C12 14.4477 12.4477 14 13 14H15C15.5523 14 16 14.4477 16 15C16 15.5523 15.5523 16 15 16H13C12.4477 16 12 15.5523 12 15Z" fill="currentColor" />
        <path fillRule="evenodd" clipRule="evenodd" d="M2 13C2 11.3431 3.34315 10 5 10H7C8.65685 10 10 11.3431 10 13V15C10 16.6569 8.65685 18 7 18H5C3.34315 18 2 16.6569 2 15V13ZM5 12C4.44772 12 4 12.4477 4 13V15C4 15.5523 4.44772 16 5 16H7C7.55228 16 8 15.5523 8 15V13C8 12.4477 7.55228 12 7 12H5Z" fill="currentColor" />
      </svg>
    ),
  },
  {
    name: 'Analytics',
    href: '/analytics',
    icon: (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M2 17C2 16.4477 2.44772 16 3 16H4C4.55228 16 5 16.4477 5 17V18H2V17Z" fill="currentColor" />
        <path d="M6 13C6 12.4477 6.44772 12 7 12H8C8.55228 12 9 12.4477 9 13V18H6V13Z" fill="currentColor" />
        <path d="M10 9C10 8.44772 10.4477 8 11 8H12C12.5523 8 13 8.44772 13 9V18H10V9Z" fill="currentColor" />
        <path d="M14 5C14 4.44772 14.4477 4 15 4H16C16.5523 4 17 4.44772 17 5V18H14V5Z" fill="currentColor" />
        <path d="M2 3L6 7.5L10 5L14 2L18 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    name: 'Settings',
    href: '/settings',
    icon: (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M8.03371 2.08545C8.19529 1.45517 8.76252 1 9.41421 1H10.5858C11.2375 1 11.8047 1.45517 11.9663 2.08545L12.2801 3.30906C12.5952 3.45447 12.8966 3.62445 13.1814 3.81688L14.3738 3.41416C14.9832 3.20829 15.6509 3.46476 15.9767 4.02918L16.5625 5.04466C16.8884 5.60908 16.777 6.31699 16.2909 6.75557L15.3562 7.59834C15.3849 7.86017 15.4 8.12593 15.4 8.39525C15.4 8.66458 15.3849 8.93034 15.3562 9.19217L16.2909 10.0349C16.777 10.4735 16.8884 11.1814 16.5625 11.7458L15.9767 12.7613C15.6509 13.3257 14.9832 13.5822 14.3738 13.3763L13.1814 12.9736C12.8966 13.166 12.5952 13.336 12.2801 13.4814L11.9663 14.705C11.8047 15.3353 11.2375 15.7905 10.5858 15.7905H9.41421C8.76252 15.7905 8.19529 15.3353 8.03371 14.705L7.71987 13.4814C7.40483 13.336 7.10341 13.166 6.81861 12.9736L5.62618 13.3763C5.01681 13.5822 4.34912 13.3257 4.02329 12.7613L3.4375 11.7458C3.11167 11.1814 3.22303 10.4735 3.70914 10.0349L4.64379 9.19217C4.61508 8.93034 4.6 8.66458 4.6 8.39525C4.6 8.12593 4.61508 7.86017 4.64379 7.59834L3.70914 6.75557C3.22303 6.31699 3.11167 5.60908 3.4375 5.04466L4.02329 4.02918C4.34912 3.46476 5.01681 3.20829 5.62618 3.41416L6.81861 3.81688C7.10341 3.62445 7.40483 3.45447 7.71987 3.30906L8.03371 2.08545ZM10 10.8953C11.3807 10.8953 12.5 9.77595 12.5 8.39525C12.5 7.01455 11.3807 5.89525 10 5.89525C8.61929 5.89525 7.5 7.01455 7.5 8.39525C7.5 9.77595 8.61929 10.8953 10 10.8953Z"
          fill="currentColor"
        />
      </svg>
    ),
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isConnected } = useSupabase();

  return (
    <aside className="fixed left-0 top-0 z-40 flex h-full w-56 flex-col border-r border-zinc-800 bg-[#0a0a0f]">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-zinc-800/50">
        <span className={cn(
          'inline-block h-2 w-2 rounded-full',
          isConnected ? 'bg-[#00c853] animate-pulse' : 'bg-red-500',
        )} />
        <span className="text-lg font-bold tracking-widest text-white">ALPHA</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map((item) => {
          const isActive = pathname === item.href;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150',
                isActive
                  ? 'bg-[#2196f3]/10 text-[#2196f3]'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/40',
              )}
            >
              <span className={cn(
                'flex-shrink-0 transition-colors',
                isActive ? 'text-[#2196f3]' : 'text-zinc-600',
              )}>
                {item.icon}
              </span>
              {item.name}
              {isActive && (
                <span className="ml-auto w-1 h-4 rounded-full bg-[#2196f3]" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-zinc-800/50">
        <div className="flex items-center gap-2">
          <span className={cn(
            'w-1.5 h-1.5 rounded-full',
            isConnected ? 'bg-[#00c853]' : 'bg-red-500',
          )} />
          <span className="text-[10px] text-zinc-600">
            {isConnected ? 'Realtime active' : 'Disconnected'}
          </span>
        </div>
        <p className="text-[10px] text-zinc-700 mt-1">Alpha Bot v1.0</p>
      </div>
    </aside>
  );
}
