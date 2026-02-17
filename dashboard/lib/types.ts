export type Exchange = 'binance' | 'delta';
export type PositionType = 'spot' | 'long' | 'short';

// Strategy values as stored in the database (lowercase)
export type Strategy = string;

export interface Trade {
  id: string;
  timestamp: string;        // normalized from opened_at
  closed_at?: string | null; // when the trade was closed
  pair: string;
  side: 'buy' | 'sell';
  price: number;            // normalized from entry_price
  exit_price?: number | null;
  amount: number;
  cost?: number;
  strategy: Strategy;
  pnl: number;
  pnl_pct?: number;
  status: 'open' | 'closed' | 'cancelled';
  exchange: Exchange;
  leverage: number;
  position_type: PositionType;
  stop_loss?: number | null;
  take_profit?: number | null;
  order_type?: string;
  reason?: string;
  order_id?: string;
  setup_type?: string;  // Entry setup classification (VWAP_RECLAIM, MOMENTUM_BURST, etc.)
}

export interface StrategyLog {
  id: string;
  created_at?: string;
  // Normalized: we map created_at → timestamp for display
  timestamp: string;
  pair: string;
  market_condition: string;
  strategy_selected: Strategy;
  reason: string;
  exchange: Exchange;
  adx?: number;
  rsi?: number;
  signal_strength?: number;
  macd_value?: number;
  macd_signal?: number;
  macd_histogram?: number;
  bb_upper?: number;
  bb_lower?: number;
  bb_middle?: number;
  bb_width?: number;
  atr?: number;
  volume_ratio?: number;
  entry_distance_pct?: number;
  current_price?: number;
  price_change_15m?: number;
  price_change_1h?: number;
  price_change_24h?: number;
  plus_di?: number;
  minus_di?: number;
  direction?: string;  // "bullish" | "bearish" | "neutral"
  // Signal state from bot's 1m scalp strategy (written by engine)
  signal_count?: number;   // 0-4: how many of 4 signals are active
  signal_side?: string;    // "long", "short", or null (scanning)
  signal_mom?: boolean;    // momentum signal active
  signal_vol?: boolean;    // volume spike signal active
  signal_rsi?: boolean;    // RSI extreme signal active
  signal_bb?: boolean;     // BB mean-reversion signal active
}

export interface BotStatus {
  id: string;
  created_at: string;
  // Normalized field — we map created_at → timestamp for display
  timestamp: string;
  total_pnl: number;
  daily_pnl?: number;
  daily_loss_pct?: number;
  win_rate: number;
  total_trades?: number;
  open_positions?: number;
  active_strategy?: Strategy;
  market_condition?: string;
  capital: number;
  pair?: string;
  is_running?: boolean;
  is_paused?: boolean;
  pause_reason?: string | null;
  binance_balance?: number;
  delta_balance?: number;
  delta_balance_inr?: number | null;
  binance_connected?: boolean;
  delta_connected?: boolean;
  bot_state?: 'running' | 'paused' | 'error';
  uptime_seconds?: number;
  shorting_enabled?: boolean;
  leverage_level?: number;
  leverage?: number;
  active_strategies_count?: number;
  active_strategy_count?: number;
}

export interface BotCommand {
  id?: string;
  timestamp?: string;
  command: 'pause' | 'resume' | 'force_strategy' | 'update_config' | 'update_pair_config';
  params: Record<string, unknown>;
  executed?: boolean;
}

export interface StrategyStats {
  strategy: Strategy;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_duration_minutes: number;
  last_active: string;
}

// --- Supabase View Types (matching actual DB columns) ---

export interface OpenPosition {
  id: string;
  opened_at: string;
  pair: string;
  side: 'buy' | 'sell';
  entry_price: number;
  amount: number;
  cost?: number;
  strategy: Strategy;
  exchange: Exchange;
  leverage: number;
  position_type: PositionType;
  effective_exposure: number;
  reason?: string;
  order_id?: string;
  // Optional fields that the view or component may provide
  current_price?: number;
  pnl?: number;
  stop_loss?: number | null;
  take_profit?: number | null;
  // Live position state (written by bot every ~10s)
  position_state?: 'holding' | 'trailing' | null;
  trail_stop_price?: number | null;
  current_pnl?: number | null;   // unrealized P&L % (price move)
  peak_pnl?: number | null;      // highest P&L % reached
}

export interface PnLByExchange {
  exchange: Exchange;
  total_trades: number;
  open_trades?: number;
  closed_trades?: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  total_pnl: number;
  avg_pnl_pct?: number;
}

export interface FuturesPosition {
  id: string;
  timestamp: string;
  pair: string;
  side: 'buy' | 'sell';
  price: number;
  amount: number;
  strategy: Strategy;
  pnl: number;
  status: 'open' | 'closed' | 'cancelled';
  exchange: Exchange;
  leverage: number;
  position_type: PositionType;
  leveraged_pnl: number;
  leveraged_pnl_pct: number;
}

export interface DailyPnL {
  trade_date: string;
  daily_pnl: number;
  spot_pnl: number;
  futures_pnl: number;
}

export interface StrategyPerformance {
  strategy: Strategy;
  exchange: Exchange;
  total_trades: number;
  closed_trades?: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  total_pnl: number;
  avg_pnl_pct?: number;
  best_trade: number;
  worst_trade: number;
}

export interface PnLByPair {
  pair: string;
  exchange: Exchange;
  position_type: PositionType;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
}

export type ExchangeFilter = 'all' | 'binance' | 'delta';

export type ActivityEventType = 'analysis' | 'strategy_switch' | 'trade_open' | 'trade_close' | 'short_open' | 'risk_alert';

export interface ActivityEvent {
  id: string;
  timestamp: string;
  pair: string;
  eventType: ActivityEventType;
  description: string;
  exchange?: Exchange;
}

export type ActivityFilter = 'all' | 'trades' | 'alerts';
