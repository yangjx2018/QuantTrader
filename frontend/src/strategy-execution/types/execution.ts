export interface Execution {
  id: number
  strategy_id: number
  strategy_name: string
  account_id: number
  status: 'running' | 'paused' | 'stopped'
  start_time: string
  end_time?: string
  total_pnl: number
  total_signals: number
  total_orders: number
  params?: Record<string, unknown>
  remark?: string
  created_at: string
  updated_at: string
}

export interface ExecutionSignal {
  id: number
  execution_id: number
  strategy_id: number
  symbol: string
  symbol_name?: string
  direction: 'buy' | 'sell'
  signal_price: number
  quantity: number
  order_type: string
  reason?: string
  risk_passed: boolean
  risk_reason?: string
  order_id?: string
  order_status?: string
  filled_price?: number
  filled_quantity?: number
  pnl?: number
  created_at: string
  updated_at: string
}

export interface RiskRule {
  id: number
  rule_type: string
  rule_name: string
  enabled: boolean
  params: Record<string, unknown>
  description?: string
  created_at: string
  updated_at: string
}

export interface RiskAlert {
  id: number
  execution_id?: number
  rule_id?: number
  rule_type: string
  severity: 'info' | 'warning' | 'critical'
  title: string
  message: string
  acknowledged: boolean
  acknowledged_at?: string
  acknowledged_by?: string
  action_taken?: string
  action_result?: string
  created_at: string
  updated_at: string
}

export interface ExecutionLog {
  id: number
  execution_id: number
  level: 'info' | 'warning' | 'error' | 'success'
  category: string
  message: string
  details?: Record<string, unknown>
  created_at: string
}

export interface ExecutionStatus {
  running_count: number
  paused_count: number
  stopped_count: number
  today_pnl: number
  active_alerts: number
  total_signals_today: number
}

export interface StartExecutionRequest {
  strategy_id: number
  account_id: number
  params?: {
    symbol?: string
    timeframe?: string
    [key: string]: unknown
  }
}

export interface ApiResponse<T> {
  success: boolean
  data: T
  message: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
