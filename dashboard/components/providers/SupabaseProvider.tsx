'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import { getSupabase } from '@/lib/supabase';
import type {
  Trade,
  BotStatus,
  StrategyLog,
  ExchangeFilter,
  OpenPosition,
  PnLByExchange,
  FuturesPosition,
  DailyPnL,
  StrategyPerformance,
  ActivityEvent,
  ActivityEventType,
  ActivityLogRow,
  OptionsState,
  PairConfig,
  SetupConfig,
  SignalState,
} from '@/lib/types';

// ---------------------------------------------------------------------------
// Normalize raw DB rows → app types (maps column name differences)
// ---------------------------------------------------------------------------

function normalizeBotStatus(raw: any): BotStatus {
  return {
    ...raw,
    timestamp: raw.created_at ?? raw.timestamp ?? '',
    // Normalize leverage field (DB uses 'leverage', some code expects 'leverage_level')
    leverage: raw.leverage ?? raw.leverage_level ?? 1,
    leverage_level: raw.leverage_level ?? raw.leverage ?? 1,
    // Normalize strategy count
    active_strategies_count: raw.active_strategy_count ?? raw.active_strategies_count ?? 0,
    active_strategy_count: raw.active_strategy_count ?? raw.active_strategies_count ?? 0,
  } as BotStatus;
}

function normalizeStrategyLog(raw: any): StrategyLog {
  return {
    ...raw,
    timestamp: raw.created_at ?? raw.timestamp ?? '',
  } as StrategyLog;
}

function normalizeTrade(raw: any): Trade {
  return {
    id: String(raw.id),
    timestamp: raw.opened_at ?? raw.timestamp ?? raw.created_at ?? '',
    closed_at: raw.closed_at ?? null,
    pair: raw.pair ?? '',
    side: raw.side ?? 'buy',
    price: raw.entry_price ?? raw.price ?? 0,
    exit_price: raw.exit_price ?? null,
    amount: raw.amount ?? 0,
    cost: raw.cost ?? undefined,
    strategy: raw.strategy ?? '',
    pnl: raw.pnl ?? 0,
    // Keep null/undefined distinct from 0 — null means "not yet calculated"
    pnl_pct: raw.pnl_pct != null ? raw.pnl_pct : undefined,
    gross_pnl: raw.gross_pnl ?? undefined,
    entry_fee: raw.entry_fee ?? undefined,
    exit_fee: raw.exit_fee ?? undefined,
    status: raw.status ?? 'open',
    exchange: raw.exchange ?? 'bybit',
    leverage: raw.leverage ?? 1,
    position_type: raw.position_type ?? 'spot',
    stop_loss: raw.stop_loss ?? null,
    take_profit: raw.take_profit ?? null,
    order_type: raw.order_type,
    reason: raw.reason,
    order_id: raw.order_id,
    setup_type: raw.setup_type ?? undefined,
    exit_reason: raw.exit_reason ?? undefined,
    position_state: raw.position_state ?? null,
    trail_stop_price: raw.trail_stop_price != null ? Number(raw.trail_stop_price) : null,
    current_pnl: raw.current_pnl != null ? Number(raw.current_pnl) : null,
    current_price: raw.current_price != null ? Number(raw.current_price) : null,
    peak_pnl: raw.peak_pnl != null ? Number(raw.peak_pnl) : null,
  };
}

interface SupabaseContextValue {
  trades: Trade[];
  recentTrades: Trade[];
  botStatus: BotStatus | null;
  strategyLog: StrategyLog[];
  isConnected: boolean;
  exchangeFilter: ExchangeFilter;
  setExchangeFilter: (filter: ExchangeFilter) => void;
  filteredTrades: Trade[];
  openPositions: OpenPosition[];
  pnlByExchange: PnLByExchange[];
  futuresPositions: FuturesPosition[];
  dailyPnL: DailyPnL[];
  strategyPerformance: StrategyPerformance[];
  activityFeed: ActivityEvent[];
  optionsLog: ActivityLogRow[];
  optionsState: OptionsState[];
  refreshViews: () => void;
  // Control Panel
  pairConfigs: PairConfig[];
  setupConfigs: SetupConfig[];
  signalStates: SignalState[];
}

const SupabaseContext = createContext<SupabaseContextValue | null>(null);

const EMPTY_CONTEXT: SupabaseContextValue = {
  trades: [],
  recentTrades: [],
  botStatus: null,
  strategyLog: [],
  isConnected: false,
  exchangeFilter: 'all',
  setExchangeFilter: () => {},
  filteredTrades: [],
  openPositions: [],
  pnlByExchange: [],
  futuresPositions: [],
  dailyPnL: [],
  strategyPerformance: [],
  activityFeed: [],
  optionsLog: [],
  optionsState: [],
  refreshViews: () => {},
  pairConfigs: [],
  setupConfigs: [],
  signalStates: [],
};

function buildActivityEvent(
  type: ActivityEventType,
  source: Trade | StrategyLog,
  description: string,
): ActivityEvent {
  return {
    id: String(source.id),
    timestamp: source.timestamp,
    pair: 'pair' in source ? source.pair : '',
    eventType: type,
    description,
    exchange: 'exchange' in source ? source.exchange : undefined,
  };
}

export function SupabaseProvider({ children }: { children: ReactNode }) {
  const client = getSupabase();

  if (!client) {
    console.warn('[Alpha] SupabaseProvider: no client — NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY missing');
    return (
      <SupabaseContext.Provider value={EMPTY_CONTEXT}>
        {children}
      </SupabaseContext.Provider>
    );
  }

  return <SupabaseProviderInner>{children}</SupabaseProviderInner>;
}

function SupabaseProviderInner({ children }: { children: ReactNode }) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [strategyLog, setStrategyLog] = useState<StrategyLog[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [exchangeFilter, setExchangeFilter] = useState<ExchangeFilter>('all');
  const [activityFeed, setActivityFeed] = useState<ActivityEvent[]>([]);
  const [optionsLog, setOptionsLog] = useState<ActivityLogRow[]>([]);
  const [optionsState, setOptionsState] = useState<OptionsState[]>([]);

  const [openPositions, setOpenPositions] = useState<OpenPosition[]>([]);
  const [pnlByExchange, setPnlByExchange] = useState<PnLByExchange[]>([]);
  const [futuresPositions, setFuturesPositions] = useState<FuturesPosition[]>([]);
  const [dailyPnL, setDailyPnL] = useState<DailyPnL[]>([]);
  const [strategyPerformance, setStrategyPerformance] = useState<StrategyPerformance[]>([]);

  // Control Panel state
  const [pairConfigs, setPairConfigs] = useState<PairConfig[]>([]);
  const [setupConfigs, setSetupConfigs] = useState<SetupConfig[]>([]);
  const [signalStates, setSignalStates] = useState<SignalState[]>([]);

  const activityRef = useRef<ActivityEvent[]>([]);

  const pushActivity = useCallback((event: ActivityEvent) => {
    activityRef.current = [event, ...activityRef.current].slice(0, 50);
    setActivityFeed([...activityRef.current]);
  }, []);

  const fetchViews = useCallback(async () => {
    const client = getSupabase();
    if (!client) return;

    try {
      const res = await client.from('v_open_positions').select('*');
      if (res.data) setOpenPositions(res.data as OpenPosition[]);
      else if (res.error) console.warn('[Alpha] v_open_positions:', res.error.message);
    } catch (e) { console.warn('[Alpha] v_open_positions fetch failed', e); }

    try {
      const res = await client.from('v_pnl_by_exchange').select('*');
      if (res.data) setPnlByExchange(res.data as PnLByExchange[]);
      else if (res.error) console.warn('[Alpha] v_pnl_by_exchange:', res.error.message);
    } catch (e) { console.warn('[Alpha] v_pnl_by_exchange fetch failed', e); }

    try {
      const res = await client.from('v_futures_positions').select('*');
      if (res.data) setFuturesPositions(res.data as FuturesPosition[]);
      else if (res.error) console.warn('[Alpha] v_futures_positions:', res.error.message);
    } catch (e) { console.warn('[Alpha] v_futures_positions fetch failed', e); }

    try {
      const res = await client.from('v_daily_pnl_timeseries').select('*').order('trade_date', { ascending: true });
      if (res.data) setDailyPnL(res.data as DailyPnL[]);
      else if (res.error) console.warn('[Alpha] v_daily_pnl_timeseries:', res.error.message);
    } catch (e) { console.warn('[Alpha] v_daily_pnl_timeseries fetch failed', e); }

    try {
      const res = await client.from('v_strategy_performance').select('*');
      if (res.data) setStrategyPerformance(res.data as StrategyPerformance[]);
      else if (res.error) console.warn('[Alpha] v_strategy_performance:', res.error.message);
    } catch (e) { console.warn('[Alpha] v_strategy_performance fetch failed', e); }

    // Control Panel tables
    try {
      const res = await client.from('pair_config').select('*');
      if (res.data) setPairConfigs(res.data as PairConfig[]);
    } catch (e) { console.warn('[Alpha] pair_config fetch failed', e); }

    try {
      const res = await client.from('setup_config').select('*');
      if (res.data) setSetupConfigs(res.data as SetupConfig[]);
    } catch (e) { console.warn('[Alpha] setup_config fetch failed', e); }

    try {
      const res = await client.from('signal_state').select('*');
      if (res.data) setSignalStates(res.data as SignalState[]);
    } catch (e) { console.warn('[Alpha] signal_state fetch failed', e); }
  }, []);

  const buildInitialFeed = useCallback((trades: Trade[], activityRows: ActivityLogRow[]) => {
    const events: ActivityEvent[] = [];

    for (const trade of trades.slice(0, 40)) {
      const exchangeLabel = trade.exchange === 'delta' ? ' on Delta' : '';
      if (trade.status === 'open') {
        const isFuturesShort = trade.position_type === 'short';
        const type: ActivityEventType = isFuturesShort ? 'short_open' : 'trade_open';
        const label = isFuturesShort
          ? `SHORT ${trade.pair} @ $${trade.price.toLocaleString()}${exchangeLabel}`
          : `${trade.side.toUpperCase()} ${trade.pair} @ $${trade.price.toLocaleString()} — ${trade.strategy}`;
        events.push(buildActivityEvent(type, trade, label));
      } else if (trade.status === 'closed') {
        const pnlLabel = trade.pnl >= 0 ? `+$${trade.pnl.toFixed(2)}` : `-$${Math.abs(trade.pnl).toFixed(2)}`;
        events.push(
          buildActivityEvent('trade_close', trade, `${trade.pair} closed — ${pnlLabel} P&L`),
        );
      }
    }

    // Options / risk events from activity_log table
    for (const row of activityRows) {
      const eventType = (['options_entry', 'options_skip', 'options_exit', 'risk_alert'].includes(row.event_type)
        ? row.event_type
        : 'options_skip') as ActivityEventType;
      events.push({
        id: String(row.id),
        timestamp: row.created_at,
        pair: row.pair,
        eventType,
        description: row.description,
        exchange: (row.exchange === 'delta' || row.exchange === 'binance') ? row.exchange : undefined,
      });
    }

    events.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
    activityRef.current = events.slice(0, 50);
    setActivityFeed([...activityRef.current]);
  }, []);

  // ---------- Fetch initial data ----------
  useEffect(() => {
    const client = getSupabase();
    if (!client) {
      console.warn('[Alpha] No Supabase client — env vars missing?');
      return;
    }

    console.log('[Alpha] Supabase client ready, fetching data...');

    /** Fetch ALL trades using pagination (Supabase caps at 1000 per request). */
    async function fetchAllTrades() {
      const PAGE = 1000;
      let allRows: any[] = [];
      let from = 0;
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { data, error } = await client!
          .from('trades')
          .select('*')
          .order('opened_at', { ascending: false })
          .range(from, from + PAGE - 1);
        if (error) { console.error('[Alpha] trades page error:', error.message); break; }
        if (!data || data.length === 0) break;
        allRows = allRows.concat(data);
        if (data.length < PAGE) break; // last page
        from += PAGE;
      }
      return allRows;
    }

    async function fetchInitialData() {
      try {
        const [allTradesData, botStatusRes, strategyLogRes, latestPerPairRes, activityLogRes, optionsStateRes] = await Promise.all([
          fetchAllTrades(),
          // bot_status uses created_at
          client!.from('bot_status').select('*').order('created_at', { ascending: false }).limit(1),
          // strategy_log — recent history for charts & MarketOverview
          client!.from('strategy_log').select('*').order('created_at', { ascending: false }).limit(200),
          // latest_strategy_log view — guaranteed 1 row per pair+exchange (for MarketOverview)
          client!.from('latest_strategy_log').select('*'),
          // activity_log — options decisions, risk alerts for Live Activity feed
          client!.from('activity_log').select('*').order('created_at', { ascending: false }).limit(50),
          // options_state — real-time options monitoring (1 row per pair)
          client!.from('options_state').select('*'),
        ]);

        if (botStatusRes.error) console.error('[Alpha] bot_status query error:', botStatusRes.error.message);
        if (strategyLogRes.error) console.error('[Alpha] strategy_log query error:', strategyLogRes.error.message);
        if (latestPerPairRes.error) console.warn('[Alpha] latest_strategy_log view error:', latestPerPairRes.error.message);
        if (activityLogRes.error) console.warn('[Alpha] activity_log query error:', activityLogRes.error.message);
        if (optionsStateRes.error) console.warn('[Alpha] options_state query error:', optionsStateRes.error.message);

        // Normalize all data (map DB column names → app types)
        const tradeData = (allTradesData ?? []).map(normalizeTrade);
        console.log(`[Alpha] Loaded ${tradeData.length} trades (paginated)`);
        const logData = (strategyLogRes.data ?? []).map(normalizeStrategyLog);

        // Merge: prepend latest-per-pair rows so MarketOverview always sees all pairs
        // Deduplicate by id — view rows may already be in the 200-row fetch
        const seenIds = new Set(logData.map(l => l.id));
        const latestRows = (latestPerPairRes.data ?? []).map(normalizeStrategyLog);
        const extraRows = latestRows.filter(r => !seenIds.has(r.id));
        const mergedLogs = [...logData, ...extraRows];
        const activityRows = (activityLogRes.data ?? []) as ActivityLogRow[];

        console.log(`[Alpha] Fetched: ${tradeData.length} trades, ${mergedLogs.length} strategy logs (${extraRows.length} from view), ${activityRows.length} activity events`);

        setTrades(tradeData);
        if (botStatusRes.data && botStatusRes.data.length > 0) {
          setBotStatus(normalizeBotStatus(botStatusRes.data[0]));
        }
        setStrategyLog(mergedLogs);
        setOptionsLog(activityRows.filter(r => r.event_type.startsWith('options_')));
        if (optionsStateRes.data) setOptionsState(optionsStateRes.data as OptionsState[]);
        buildInitialFeed(tradeData, activityRows);
      } catch (err) {
        console.error('[Alpha] fetchInitialData failed:', err);
      }
    }

    fetchInitialData();
    fetchViews();

    // Poll every 60s as fallback (realtime may disconnect silently)
    const pollInterval = setInterval(async () => {
      try {
        const [logRes, latestRes, statusRes, allTradeRows] = await Promise.all([
          client!.from('strategy_log').select('*').order('created_at', { ascending: false }).limit(200),
          client!.from('latest_strategy_log').select('*'),
          client!.from('bot_status').select('*').order('created_at', { ascending: false }).limit(1),
          fetchAllTrades(),
        ]);

        if (logRes.data) {
          const logs = logRes.data.map(normalizeStrategyLog);
          // Merge latest-per-pair so all pairs always appear
          const seenIds = new Set(logs.map(l => l.id));
          const extra = (latestRes.data ?? []).map(normalizeStrategyLog).filter(r => !seenIds.has(r.id));
          setStrategyLog([...logs, ...extra]);
        }

        if (statusRes.data && statusRes.data.length > 0) setBotStatus(normalizeBotStatus(statusRes.data[0]));
        if (allTradeRows) setTrades(allTradeRows.map(normalizeTrade));
      } catch (e) { console.warn('[Alpha] Poll refresh failed', e); }

      fetchViews();
    }, 60_000);

    return () => clearInterval(pollInterval);
  }, [fetchViews, buildInitialFeed]);

  // ---------- Realtime subscriptions ----------
  useEffect(() => {
    const client = getSupabase();
    if (!client) return;

    const channel = client
      .channel('alpha-dashboard')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'trades' }, (payload) => {
        const t = normalizeTrade(payload.new);
        setTrades((prev) => [t, ...prev]);
        fetchViews();

        if (t.status === 'open') {
          const isFuturesShort = t.position_type === 'short';
          pushActivity(
            buildActivityEvent(
              isFuturesShort ? 'short_open' : 'trade_open',
              t,
              isFuturesShort
                ? `SHORT ${t.pair} @ $${t.price.toLocaleString()} on ${t.exchange}`
                : `${t.side.toUpperCase()} ${t.pair} @ $${t.price.toLocaleString()} — ${t.strategy}`,
            ),
          );
        } else if (t.status === 'closed') {
          const pctLabel = t.pnl >= 0 ? `+${t.pnl.toFixed(2)}%` : `${t.pnl.toFixed(2)}%`;
          pushActivity(buildActivityEvent('trade_close', t, `${t.pair} ${pctLabel} profit closed`));
        }
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'trades' }, (payload) => {
        const updated = normalizeTrade(payload.new);
        setTrades((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
        fetchViews();

        if (updated.status === 'closed') {
          const pctLabel = updated.pnl >= 0 ? `+${updated.pnl.toFixed(2)}%` : `${updated.pnl.toFixed(2)}%`;
          pushActivity(buildActivityEvent('trade_close', updated, `${updated.pair} ${pctLabel} closed`));
        }
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'bot_status' }, (payload) => {
        setBotStatus(normalizeBotStatus(payload.new));
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'bot_status' }, (payload) => {
        setBotStatus(normalizeBotStatus(payload.new));
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'strategy_log' }, (payload) => {
        const log = normalizeStrategyLog(payload.new);
        setStrategyLog((prev) => [log, ...prev]);
        // No longer push analysis events to activity feed — replaced by activity_log
      })
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'activity_log' }, (payload) => {
        const row = payload.new as ActivityLogRow;
        // Update options log (keep last 50 options events)
        if (row.event_type.startsWith('options_')) {
          setOptionsLog((prev) => [row, ...prev].slice(0, 50));
        }
        const eventType = (['options_entry', 'options_skip', 'options_exit', 'risk_alert'].includes(row.event_type)
          ? row.event_type
          : 'options_skip') as ActivityEventType;
        pushActivity({
          id: String(row.id),
          timestamp: row.created_at,
          pair: row.pair,
          eventType,
          description: row.description,
          exchange: (row.exchange === 'delta' || row.exchange === 'binance') ? row.exchange : undefined,
        });
      })
      // Options state realtime (engine upserts every ~30s)
      .on('postgres_changes', { event: '*', schema: 'public', table: 'options_state' }, (payload) => {
        const row = payload.new as OptionsState;
        setOptionsState((prev) => {
          const idx = prev.findIndex((s) => s.pair === row.pair);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = row;
            return next;
          }
          return [...prev, row];
        });
      })
      // Control Panel realtime
      .on('postgres_changes', { event: '*', schema: 'public', table: 'pair_config' }, () => {
        client!.from('pair_config').select('*').then(res => { if (res.data) setPairConfigs(res.data as PairConfig[]); });
      })
      .on('postgres_changes', { event: '*', schema: 'public', table: 'setup_config' }, () => {
        client!.from('setup_config').select('*').then(res => { if (res.data) setSetupConfigs(res.data as SetupConfig[]); });
      })
      .on('postgres_changes', { event: '*', schema: 'public', table: 'signal_state' }, () => {
        client!.from('signal_state').select('*').then(res => { if (res.data) setSignalStates(res.data as SignalState[]); });
      })
      .subscribe((status) => {
        setIsConnected(status === 'SUBSCRIBED');
      });

    return () => {
      client.removeChannel(channel);
    };
  }, [fetchViews, pushActivity]);

  const filteredTrades = useMemo(() => {
    if (exchangeFilter === 'all') return trades;
    return trades.filter((t) => t.exchange === exchangeFilter);
  }, [trades, exchangeFilter]);

  const recentTrades = useMemo(() => filteredTrades.slice(0, 10), [filteredTrades]);

  const value = useMemo<SupabaseContextValue>(
    () => ({
      trades,
      recentTrades,
      botStatus,
      strategyLog,
      isConnected,
      exchangeFilter,
      setExchangeFilter,
      filteredTrades,
      openPositions,
      pnlByExchange,
      futuresPositions,
      dailyPnL,
      strategyPerformance,
      activityFeed,
      optionsLog,
      optionsState,
      refreshViews: fetchViews,
      pairConfigs,
      setupConfigs,
      signalStates,
    }),
    [
      trades, recentTrades, botStatus, strategyLog, isConnected,
      exchangeFilter, filteredTrades,
      openPositions, pnlByExchange, futuresPositions, dailyPnL, strategyPerformance,
      activityFeed, optionsLog, optionsState, fetchViews,
      pairConfigs, setupConfigs, signalStates,
    ],
  );

  return (
    <SupabaseContext.Provider value={value}>
      {children}
    </SupabaseContext.Provider>
  );
}

export function useSupabase(): SupabaseContextValue {
  const context = useContext(SupabaseContext);
  if (!context) {
    throw new Error('useSupabase must be used within a <SupabaseProvider>');
  }
  return context;
}
