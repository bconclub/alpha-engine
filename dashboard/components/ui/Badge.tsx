import { cn } from '@/lib/utils';

const variantStyles = {
  default: 'bg-zinc-700/50 text-zinc-300',
  success: 'bg-emerald-400/10 text-emerald-400',
  danger: 'bg-red-400/10 text-red-400',
  warning: 'bg-amber-400/10 text-amber-400',
  purple: 'bg-purple-400/10 text-purple-400',
  blue: 'bg-blue-400/10 text-blue-400',
} as const;

interface BadgeProps {
  variant: keyof typeof variantStyles;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant, children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
