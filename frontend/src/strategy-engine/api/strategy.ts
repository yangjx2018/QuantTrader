/**
 * strategy-engine 模块的 API 请求函数。
 *
 * 与后端 `backend/strategy_engine/router.py` 路由严格对齐。
 *
 * 注意：request 实例的响应拦截器已解包到 `ApiResponse.data`，
 * 因此 `request.get<T>(...)` 直接返回 `Promise<T>`（业务数据本身）。
 * 后端 list 接口的 `total` 顶层字段在拦截器后被丢弃，前端用 `length` 替代。
 */

import request from '@/common/utils/request'
import type {
  DryRunRequestInput,
  DryRunResult,
  Strategy,
  StrategyCreateInput,
  StrategyOption,
  StrategyUpdateInput,
  StrategyValidateRequest,
  StrategyValidateResult,
  StrategyVersion,
} from '../types/strategy'

/** 状态过滤参数 */
export interface StrategyListParams {
  status?: string
  strategy_type?: string
  limit?: number
  offset?: number
}

// ============================================================
// 策略 CRUD
// ============================================================

export function getStrategies(params?: StrategyListParams) {
  return request.get<Strategy[]>('/strategy/list', { params })
}

export function getStrategy(id: number) {
  return request.get<Strategy>(`/strategy/${id}`)
}

export function createStrategy(data: StrategyCreateInput) {
  return request.post<Strategy>('/strategy/create', data)
}

export function updateStrategy(id: number, data: StrategyUpdateInput) {
  return request.put<Strategy>(`/strategy/${id}`, data)
}

export function deleteStrategy(id: number) {
  return request.delete<{ deleted_versions: number }>(`/strategy/${id}`)
}

// ============================================================
// 简化列表 / 校验 / 试运行
// ============================================================

export function getStrategyOptions(status: 'active' | 'all' = 'active') {
  return request.get<StrategyOption[]>('/strategy/options/all', {
    params: { status },
  })
}

export function validateStrategyCode(payload: StrategyValidateRequest) {
  return request.post<StrategyValidateResult>('/strategy/validate', payload)
}

export function dryRunStrategy(id: number, payload: DryRunRequestInput) {
  return request.post<DryRunResult>(`/strategy/${id}/dry-run`, payload)
}

// ============================================================
// 策略版本
// ============================================================

export function getStrategyVersions(strategyId: number, status?: string) {
  return request.get<StrategyVersion[]>(`/strategy/${strategyId}/versions`, {
    params: { status },
  })
}

export function createStrategyVersion(
  strategyId: number,
  data: Omit<StrategyVersion, 'id' | 'created_at' | 'strategy_id'> & { strategy_id?: number },
) {
  return request.post<StrategyVersion>(`/strategy/${strategyId}/versions`, data)
}
