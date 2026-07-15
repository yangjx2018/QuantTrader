/**
 * strategy-engine 模块的状态管理 Hook（基于 useState + useEffect，简洁版）。
 *
 * 使用方式：
 *   const { strategies, loading, error, refresh } = useStrategies({ status: 'active' })
 *
 * 后续如需全局共享状态（编辑器 / 列表同步），可升级为 Zustand store。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { getStrategies, type StrategyListParams } from '../api/strategy'
import type { Strategy, StrategyStatus } from '../types/strategy'

interface UseStrategiesOptions extends StrategyListParams {
  /** 是否在挂载时自动加载，默认 true */
  autoFetch?: boolean
}

interface UseStrategiesResult {
  strategies: Strategy[]
  /** 当前过滤条件下的条数（前端 length，后端 total 字段已被响应拦截器丢弃） */
  total: number
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  setStatusFilter: (status: StrategyStatus | 'all') => void
  statusFilter: StrategyStatus | 'all'
}

export function useStrategies(options: UseStrategiesOptions = {}): UseStrategiesResult {
  const { autoFetch = true, limit = 100, offset = 0, strategy_type } = options

  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StrategyStatus | 'all'>(
    (options.status as StrategyStatus | 'all') || 'all',
  )

  // 防止竞态：保留最新请求 token
  const requestIdRef = useRef(0)

  const refresh = useCallback(async () => {
    const reqId = ++requestIdRef.current
    setLoading(true)
    setError(null)
    try {
      const params: StrategyListParams = {
        limit,
        offset,
        strategy_type,
      }
      if (statusFilter !== 'all') {
        params.status = statusFilter
      }
      const items = await getStrategies(params)
      // 只接受最新请求的结果
      if (reqId !== requestIdRef.current) return
      setStrategies(items)
      setTotal(items.length)
    } catch (err) {
      if (reqId !== requestIdRef.current) return
      const message = err instanceof Error ? err.message : '加载策略列表失败'
      setError(message)
      setStrategies([])
      setTotal(0)
    } finally {
      if (reqId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [limit, offset, strategy_type, statusFilter])

  useEffect(() => {
    if (autoFetch) {
      void refresh()
    }
  }, [autoFetch, refresh, statusFilter])

  return {
    strategies,
    total,
    loading,
    error,
    refresh,
    setStatusFilter,
    statusFilter,
  }
}
