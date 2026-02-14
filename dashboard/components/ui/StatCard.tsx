import { cn } from '@/lib/utils';

interface StatCardProps {
  title: string;
  value: string;
  change?: string;
  changeType?: 'positive' | 'negative' | 'neutral';
  icon?: React.ReactNode;
  className?: string;
}

const changeColors = {
  positive: 'text-emerald-400',
  negative: 'text-red-400',
  neutral: 'text-zinc-400',
};

export function StatCard({
  title,
  value,
  change,
  changeType = 'neutral',
  icon,
  className,
}: StatCardProps) {
  return (
    <div
      className={cn(
        'bg-card border border-zinc-800 rounded-xl p-5',
        className
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-sm text-zinc-400 uppercase tracking-wider">
          {title}
        </p>
        {icon && (
          <div className="text-zinc-500">{icon}</div>
        )}
      </div>

      <p className="mt-2 text-2xl font-bold font-mono text-white">{value}</p>

      {change && (
        <p className={cn('mt-1 text-sm font-medium', changeColors[changeType])}>
          {changeType === 'positive' && '+'}{change}
        </p>
      )}
    </div>
  );
}
