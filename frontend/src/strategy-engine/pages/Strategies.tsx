/**
 * 策略列表页（P1 MVP）。
 *
 * 功能：
 * - 表格展示全部策略（含 4 个内置）
 * - 状态过滤下拉（全部 / 草稿 / 已启用 / 已归档）
 * - 类型过滤下拉（全部 / 趋势 / 均值回归 / 套利 / 情绪）
 * - 顶部"刷新" + "新建策略"按钮
 * - 每行操作：试运行 / 编辑 / 删除（删除需二次确认）
 * - 删除确认弹窗（确认后级联删除历史版本）
 *
 * 路由：/strategies
 */

import { AppLayout } from '@/common/components'
import {
  AlertCircle,
  Brain,
  Loader2,
  Plus,
  RefreshCcw,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { deleteStrategy } from '../api/strategy'
import { StrategyTable, TYPE_LABELS } from '../components/StrategyTable'
import { useStrategies } from '../hooks/useStrategies'
import type { Strategy, StrategyStatus } from '../types/strategy'

const STATUS_OPTIONS: Array<{ value: StrategyStatus | 'all'; label: string }> = [
  { value: 'all', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '已启用' },
  { value: 'archived', label: '已归档' },
]

const TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部类型' },
  ...Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label })),
]

const selectClass =
  'h-9 px-3 rounded-md border border-outline bg-surface-container text-sm text-on-surface outline-none focus:border-primary transition-colors'

export default function Strategies() {
  const navigate = useNavigate()
  const {
    strategies,
    total,
    loading,
    error,
    refresh,
    setStatusFilter,
    statusFilter,
  } = useStrategies({ limit: 100 })

  const [typeFilter, setTypeFilter] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; msg: string } | null>(
    null,
  )
  const [confirmDelete, setConfirmDelete] = useState<Strategy | null>(null)

  const showToast = useCallback(
    (type: 'success' | 'error' | 'info', msg: string) => {
      setToast({ type, msg })
      // 3 秒后自动消失
      window.setTimeout(() => setToast(null), 3000)
    },
    [],
  )

  const handleEdit = useCallback(
    (s: Strategy) => {
      navigate(`/strategy-editor?id=${s.id}`)
    },
    [navigate],
  )

  const handleDryRun = useCallback(
    (s: Strategy) => {
      // 跳转编辑器并携带 dry-run 触发标志
      navigate(`/strategy-editor?id=${s.id}&dryRun=1`)
    },
    [navigate],
  )

  const handleDeleteClick = useCallback((s: Strategy) => {
    setConfirmDelete(s)
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (!confirmDelete) return
    setBusy(true)
    try {
      const result = await deleteStrategy(confirmDelete.id)
      showToast(
        'success',
        `已删除策略 ${confirmDelete.code}（含 ${result?.deleted_versions ?? 0} 个版本）`,
      )
      setConfirmDelete(null)
      await refresh()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '删除失败'
      showToast('error', msg)
    } finally {
      setBusy(false)
    }
  }, [confirmDelete, refresh, showToast])

  const handleCreate = useCallback(() => {
    navigate('/strategy-editor?new=1')
  }, [navigate])

  // 应用类型过滤（前端过滤；status 走 hook 后端过滤）
  const visibleStrategies = typeFilter
    ? strategies.filter((s) => s.strategy_type === typeFilter)
    : strategies

  return (
    <AppLayout>
      <div className="min-h-full p-6 space-y-4">
        {/* 标题区 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-primary/20 flex items-center justify-center">
              <Brain className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-on-surface">策略管理</h1>
              <p className="text-sm text-on-surface-variant">
                共 {total} 个策略 · 用 JoinQuant 风格 Python DSL 编写
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="px-3 h-9 rounded-md border border-outline bg-surface-container hover:bg-surface-container-highest text-on-surface text-sm transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              <RefreshCcw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <button
              type="button"
              onClick={handleCreate}
              className="px-4 h-9 rounded-md bg-primary hover:bg-primary-container text-primary-foreground text-sm font-medium transition-colors flex items-center gap-1.5"
            >
              <Plus className="w-4 h-4" />
              新建策略
            </button>
          </div>
        </div>

        {/* 过滤栏 */}
        <div className="flex items-center gap-3">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StrategyStatus | 'all')}
            className={selectClass}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className={selectClass}
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <div className="text-sm text-on-surface-variant">
            显示 <span className="font-mono-num text-on-surface">{visibleStrategies.length}</span> 条
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="p-3 rounded-md border border-error/30 bg-error/10 text-error flex items-center gap-2 text-sm">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
            <button
              type="button"
              onClick={() => void refresh()}
              className="ml-auto px-2 py-0.5 rounded text-xs hover:bg-error/20"
            >
              重试
            </button>
          </div>
        )}

        {/* 表格 */}
        <StrategyTable
          strategies={visibleStrategies}
          loading={loading}
          onEdit={handleEdit}
          onDryRun={handleDryRun}
          onDelete={handleDeleteClick}
          emptyText={loading ? '加载中...' : '当前过滤条件下无策略'}
        />
      </div>

      {/* 删除确认弹窗 */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-background/80 flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-surface-container-high rounded-lg border border-outline-variant shadow-float p-6 space-y-4">
            <h3 className="text-lg font-semibold text-on-surface">确认删除策略</h3>
            <p className="text-sm text-on-surface-variant">
              即将删除策略 <span className="text-on-surface font-medium">{confirmDelete.name}</span>
              （<code className="text-on-surface font-mono text-xs">{confirmDelete.code}</code>）。
            </p>
            <p className="text-sm text-error">
              此操作会级联删除其全部历史版本，不可恢复。
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                disabled={busy}
                className="px-4 h-9 rounded-md border border-outline text-on-surface text-sm hover:bg-surface-container transition-colors disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmDelete()}
                disabled={busy}
                className="px-4 h-9 rounded-md bg-error hover:bg-error/90 text-white text-sm font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
              >
                {busy && <Loader2 className="w-4 h-4 animate-spin" />}
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-md shadow-float text-sm flex items-center gap-2 animate-in fade-in slide-in-from-bottom-2"
          style={{
            backgroundColor:
              toast.type === 'success' ? 'rgba(34,197,94,0.15)' : toast.type === 'error' ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
            color: toast.type === 'success' ? '#22c55e' : toast.type === 'error' ? '#ef4444' : '#3b82f6',
            border: '1px solid currentColor',
          }}
        >
          {toast.msg}
        </div>
      )}
    </AppLayout>
  )
}
