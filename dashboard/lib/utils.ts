import { format, formatDistanceToNow } from 'date-fns';
import type { Exchange, PositionType } from './types';

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPnL(value: number): string {
  const formatted = formatCurrency(Math.abs(value));
  return value >= 0 ? `+${formatted}` : `-${formatted}`;
}

export function formatPercentage(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function formatDate(timestamp: string): string {
  return format(new Date(timestamp), 'MMM dd, yyyy HH:mm:ss');
}

export function formatShortDate(timestamp: string): string {
  return format(new Date(timestamp), 'MMM dd HH:mm');
}

export function formatTimeAgo(timestamp: string): string {
  return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
}

export function formatNumber(value: number, decimals = 2): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function normalizeStrategy(strategy: string): string {
  return strategy.toLowerCase();
}

export function getStrategyLabel(strategy: string): string {
  switch (normalizeStrategy(strategy)) {
    case 'grid': return 'Grid';
    case 'momentum': return 'Momentum';
    case 'arbitrage': return 'Arbitrage';
    case 'futures_momentum': return 'Futures Momentum';
    case 'scalp': return 'Scalp';
    default: return strategy;
  }
}

export function getStrategyColor(strategy: string): string {
  switch (normalizeStrategy(strategy)) {
    case 'grid': return '#3b82f6';
    case 'momentum': return '#f59e0b';
    case 'arbitrage': return '#8b5cf6';
    case 'futures_momentum': return '#f97316';
    case 'scalp': return '#00bcd4';
    default: return '#6b7280';
  }
}

export function getStrategyBadgeVariant(strategy: string): 'blue' | 'warning' | 'purple' | 'default' {
  switch (normalizeStrategy(strategy)) {
    case 'grid': return 'blue';
    case 'momentum': return 'warning';
    case 'arbitrage': return 'purple';
    case 'futures_momentum': return 'warning';
    case 'scalp': return 'blue';
    default: return 'default';
  }
}

export function getPnLColor(value: number): string {
  if (value > 0) return 'text-emerald-400';
  if (value < 0) return 'text-red-400';
  return 'text-zinc-400';
}

export function getPnLBg(value: number): string {
  if (value > 0) return 'bg-emerald-400/10 text-emerald-400';
  if (value < 0) return 'bg-red-400/10 text-red-400';
  return 'bg-zinc-400/10 text-zinc-400';
}

export function tradesToCSV(trades: Array<Record<string, unknown>>): string {
  if (trades.length === 0) return '';
  const headers = Object.keys(trades[0]);
  const rows = trades.map(t => headers.map(h => JSON.stringify(t[h] ?? '')).join(','));
  return [headers.join(','), ...rows].join('\n');
}

// --- Exchange / Futures helpers ---

export function getExchangeLabel(exchange: Exchange): string {
  switch (exchange) {
    case 'binance': return 'Binance';
    case 'delta': return 'Delta';
    default: return exchange;
  }
}

export function getExchangeColor(exchange: Exchange): string {
  switch (exchange) {
    case 'binance': return '#f0b90b';
    case 'delta': return '#00d2ff';
    default: return '#6b7280';
  }
}

export function getPositionTypeLabel(pt: PositionType): string {
  switch (pt) {
    case 'spot': return 'SPOT';
    case 'long': return 'LONG';
    case 'short': return 'SHORT';
    default: return pt;
  }
}

export function getPositionTypeColor(pt: PositionType): string {
  switch (pt) {
    case 'long': return 'text-emerald-400';
    case 'short': return 'text-red-400';
    case 'spot': return 'text-zinc-400';
    default: return 'text-zinc-400';
  }
}

export function getPositionTypeBadgeVariant(pt: PositionType): 'success' | 'danger' | 'default' {
  switch (pt) {
    case 'long': return 'success';
    case 'short': return 'danger';
    case 'spot': return 'default';
    default: return 'default';
  }
}

export function formatLeverage(leverage: number): string {
  return leverage > 1 ? `${leverage}x` : '';
}
