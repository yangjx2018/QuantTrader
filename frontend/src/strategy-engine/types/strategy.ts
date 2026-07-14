/**
 * strategy-engine 模块私有类型定义。
 *
 * 字段与后端 strategy_engine/schemas.py 严格对齐。
 */

/** 策略状态机：草稿 → 启用 → 归档 */
export type StrategyStatus = 'draft' | 'active' | 'archived'

/** 策略类型枚举（与后端 strategy_type 字段一致） */
export type StrategyType =
  | 'trend'
  | 'mean_reversion'
  | 'arbitrage'
  | 'sentiment'
  | string

/** 策略主档（与后端 StrategyResponse 对齐） */
export interface Strategy {
  id: number
  code: string
  name: string
  strategy_type: StrategyType
  description?: string | null
  status: StrategyStatus
  version: string
  /** 当前生效的 Python 策略代码 */
  code_content?: string | null
  /** 默认参数 */
  parameters?: Record<string, unknown> | null
  tags?: string[] | null
  author?: string | null
  is_default: boolean
  created_at: string
  updated_at: string
}

/** 创建策略请求体 */
export interface StrategyCreateInput {
  code: string
  name: string
  strategy_type: StrategyType
  description?: string
  status?: StrategyStatus
  version?: string
  code_content?: string | null
  parameters?: Record<string, unknown> | null
  tags?: string[]
  author?: string
  is_default?: boolean
}

/** 更新策略请求体（所有字段可选） */
export interface StrategyUpdateInput {
  name?: string
  strategy_type?: StrategyType
  description?: string
  status?: StrategyStatus
  version?: string
  code_content?: string | null
  parameters?: Record<string, unknown> | null
  tags?: string[]
  author?: string
  is_default?: boolean
}

/** 策略简化项（给下拉框用） */
export interface StrategyOption {
  id: number
  name: string
  description?: string | null
  strategy_type?: StrategyType | null
}

/** 策略列表响应（含 total） */
export interface StrategyListResponse {
  items: Strategy[]
  total: number
}

/** 策略版本（与后端 StrategyVersionResponse 对齐） */
export interface StrategyVersion {
  id: number
  strategy_id: number
  version: string
  change_log?: string | null
  code_content?: string | null
  parameters?: Record<string, unknown> | null
  status: string
  backtest_result?: Record<string, unknown> | null
  created_at: string
}

// ============================================================
// 校验与试运行
// ============================================================

export type ValidationSeverity = 'error' | 'warning'

export interface ValidationIssue {
  line?: number | null
  column?: number | null
  severity: ValidationSeverity
  /** 错误码：SYNTAX_ERROR / FORBIDDEN_IMPORT / MISSING_HOOK / LOAD_ERROR / RUNTIME_ERROR 等 */
  code: string
  message: string
}

export interface StrategyValidateResult {
  valid: boolean
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
}

export interface StrategyValidateRequest {
  code_content: string
  parameters?: Record<string, unknown> | null
}

/** dry-run 单 bar 简要 */
export interface DryRunBarSummary {
  time: string
  close: number
  total_assets: number
  signal: 'buy' | 'sell' | null
  orders_count: number
}

/** dry-run 响应 */
export interface DryRunResult {
  session_id: string
  total_bars: number
  time_elapsed: number
  final_capital: number
  total_return_pct: number
  bars: DryRunBarSummary[]
}

export interface DryRunRequestInput {
  stock_code: string
  start_date: string
  end_date: string
  parameters?: Record<string, unknown> | null
  max_bars?: number
}

// ============================================================
// 统一响应（与 @/common/types 一致，本地复用）
// ============================================================

export interface ApiResponse<T = unknown> {
  success: boolean
  data: T
  message?: string
  total?: number
}
