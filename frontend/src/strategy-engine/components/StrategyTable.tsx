/**
 * 策略列表表格组件。
 *
 * 列：ID / 名称+编码 / 类型 / 状态 / 版本 / 更新时间 / 操作
 *
 * 操作按钮：
 * - 编辑（跳转编辑器）
 * - 试运行（跳转编辑器并触发 dry-run）
 * - 删除（确认后删除）
 */

import {
  Archive,
  CheckCircle2,
  Edit3,
  FileCode2,
  PlayCircle,
  Trash2,
} from 'lucide-react'

import type { Strategy, StrategyStatus } from '../types/strategy'

interface StrategyTableProps {
  strategies: Strategy[]
  loading?: boolean
  onEdit?: (strategy: Strategy) => void
  onDryRun?: (strategy: Strategy) => void
  onDelete?: (strategy: Strategy) => void
  /** 可选：自定义空状态文案 */
  emptyText?: string
}

const STATUS_LABELS: Record<StrategyStatus, string> = {
  draft: '草稿',
  active: '已启用',
  archived: '已归档',
}

const STATUS_STYLES: Record<StrategyStatus, string> = {
  draft: 'bg-slate-500/20 text-slate-300 border-slate-500/30',
  active: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  archived: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30',
}

const TYPE_LABELS: Record<string, string> = {
  trend: '趋势跟随',
  mean_reversion: '均值回归',
  arbitrage: '套利',
  sentiment: '情绪驱动',
}

function getStatusIcon(status: StrategyStatus) {
  switch (status) {
    case 'active':
      return <CheckCircle2 className="w-3 h-3" />
    case 'archived':
      return <Archive className="w-3 h-3" />
    default:
      return null
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export function StrategyTable({
  strategies,
  loading = false,
  onEdit,
  onDryRun,
  onDelete,
  emptyText = '暂无策略',
}: StrategyTableProps) {
  return (
    <div className="bg-surface-container-high rounded-lg border border-outline-variant/20 overflow-hidden">
      {strategies.length === 0 ? (
        <div className="p-12 text-center text-on-surface-variant">
          <FileCode2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p>{loading ? '加载中...' : emptyText}</p>
          {!loading && <p className="text-sm mt-1">点击右上角"新建策略"开始</p>}
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-on-surface-variant bg-surface-container/50 border-b border-outline-variant/20">
              <th className="px-4 py-3 font-medium w-16">ID</th>
              <th className="px-4 py-3 font-medium">名称 / 编码</th>
              <th className="px-4 py-3 font-medium w-28">类型</th>
              <th className="px-4 py-3 font-medium w-24">状态</th>
              <th className="px-4 py-3 font-medium w-20">版本</th>
              <th className="px-4 py-3 font-medium w-36">更新时间</th>
              <th className="px-4 py-3 font-medium w-44 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-outline-variant/10">
            {strategies.map((s) => {
              const typeLabel = TYPE_LABELS[s.strategy_type] ?? s.strategy_type
              return (
                <tr
                  key={s.id}
                  className="hover:bg-surface-container/50 transition-colors"
                >
                  <td className="px-4 py-3 text-on-surface-variant font-mono-num">{s.id}</td>
                  <td className="px-4 py-3">
                    <div className="text-on-surface font-medium">{s.name}</div>
                    <div className="text-xs text-on-surface-variant font-mono mt-0.5">
                      {s.code}
                      {s.is_default && (
                        <span className="ml-2 px-1.5 py-0.5 text-xs rounded bg-primary/20 text-primary border border-primary/30">
                          默认
                        </span>
                      )}
                    </div>
                    {s.description && (
                      <div className="text-xs text-on-surface-variant mt-1 line-clamp-1">
                        {s.description}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-on-surface-variant">{typeLabel}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border ${STATUS_STYLES[s.status]}`}
                    >
                      {getStatusIcon(s.status)}
                      {STATUS_LABELS[s.status]}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-on-surface-variant font-mono-num">{s.version}</td>
                  <td className="px-4 py-3 text-on-surface-variant font-mono-num text-xs">
                    {formatDate(s.updated_at)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {onDryRun && (
                        <button
                          type="button"
                          onClick={() => onDryRun(s)}
                          title="试运行"
                          className="p-1.5 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-primary transition-colors"
                        >
                          <PlayCircle className="w-4 h-4" />
                        </button>
                      )}
                      {onEdit && (
                        <button
                          type="button"
                          onClick={() => onEdit(s)}
                          title="编辑"
                          className="p-1.5 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-primary transition-colors"
                        >
                          <Edit3 className="w-4 h-4" />
                        </button>
                      )}
                      {onDelete && (
                        <button
                          type="button"
                          onClick={() => onDelete(s)}
                          title="删除"
                          className="p-1.5 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-error transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

export { TYPE_LABELS, STATUS_LABELS }
