/**
 * 校验结果面板：展示 StrategyValidateResult。
 *
 * 业务用途：
 *   用户点"校验"按钮后，后端返回 { valid, errors, warnings }。
 *   本组件按 severity 分组展示，让用户定位问题。
 *
 * 特性：
 * - errors 用红色，warnings 用黄色
 * - 每条问题显示 line / code / message
 * - 空状态：valid=true 且无 warnings 时显示绿色"通过"
 */

import { AlertCircle, AlertTriangle, CheckCircle2 } from 'lucide-react'

import type { StrategyValidateResult } from '../types/strategy'

interface ValidationPanelProps {
  result: StrategyValidateResult | null
  /** 是否正在校验中 */
  loading?: boolean
}

export function ValidationPanel({ result, loading = false }: ValidationPanelProps) {
  if (loading) {
    return (
      <div className="p-3 rounded-md border border-outline-variant/30 bg-surface-container text-sm text-on-surface-variant">
        校验中...
      </div>
    )
  }

  if (!result) {
    return (
      <div className="p-3 rounded-md border border-outline-variant/30 bg-surface-container text-sm text-on-surface-variant">
        点击"校验"按钮检查代码语法与沙箱可加载性。
      </div>
    )
  }

  const totalIssues = result.errors.length + result.warnings.length
  if (totalIssues === 0) {
    return (
      <div className="p-3 rounded-md border border-success/30 bg-success/10 text-success flex items-center gap-2 text-sm">
        <CheckCircle2 className="w-4 h-4" />
        <span>校验通过：语法正确，沙箱可加载，钩子齐全。</span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {result.errors.map((issue, idx) => (
        <IssueItem key={`err-${idx}`} issue={issue} />
      ))}
      {result.warnings.map((issue, idx) => (
        <IssueItem key={`warn-${idx}`} issue={issue} />
      ))}
    </div>
  )
}

function IssueItem({
  issue,
}: {
  issue: import('../types/strategy').ValidationIssue
}) {
  const isError = issue.severity === 'error'
  const Icon = isError ? AlertCircle : AlertTriangle
  const colorClass = isError ? 'text-error' : 'text-warning'
  const bgClass = isError ? 'bg-error/10 border-error/30' : 'bg-warning/10 border-warning/30'

  return (
    <div className={`p-2.5 rounded-md border ${bgClass} text-xs`}>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className={`w-3.5 h-3.5 ${colorClass} shrink-0`} />
        <span className={`font-mono ${colorClass}`}>{issue.code}</span>
        {issue.line != null && (
          <span className="text-on-surface-variant">· 行 {issue.line}</span>
        )}
      </div>
      <div className="text-on-surface break-all pl-5">{issue.message}</div>
    </div>
  )
}
