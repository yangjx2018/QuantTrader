/**
 * 参数编辑器：JSON 文本 → 解析 → 错误提示。
 *
 * 业务用途：
 *   策略 parameters 字段是 JSON（如 {"short_window": 5, "long_window": 10}）。
 *   本组件让用户用 textarea 编辑，实时校验 JSON 合法性，
 *   并把解析结果通过 onChange 回传给父组件。
 *
 * 特性：
 * - 受控组件：value (object | null) + onChange (object | null)
 * - JSON 解析失败时显示红色错误提示
 * - 支持空值（合法的 null）
 * - 支持直接编辑原始 JSON 文本
 */

import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, HelpCircle } from 'lucide-react'

interface ParamsEditorProps {
  value: Record<string, unknown> | null | undefined
  onChange?: (value: Record<string, unknown> | null) => void
  /** 是否禁用 */
  disabled?: boolean
}

export function ParamsEditor({ value, onChange, disabled = false }: ParamsEditorProps) {
  // 内部维护字符串状态，便于用户编辑过程中容忍 JSON 解析失败
  const [text, setText] = useState<string>(() =>
    value == null ? '' : JSON.stringify(value, null, 2),
  )
  const [parseError, setParseError] = useState<string | null>(null)

  // 外部 value 变化时同步（如切换策略）
  useEffect(() => {
    const newText = value == null ? '' : JSON.stringify(value, null, 2)
    // 只在文本确实不同时更新，避免覆盖用户正在输入的内容
    if (newText !== text) {
      setText(newText)
      setParseError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const handleTextChange = useCallback(
    (newText: string) => {
      setText(newText)
      if (!newText.trim()) {
        setParseError(null)
        onChange?.(null)
        return
      }
      try {
        const parsed = JSON.parse(newText)
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('参数必须是 JSON 对象（不能是数组或基础类型）')
        }
        setParseError(null)
        onChange?.(parsed as Record<string, unknown>)
      } catch (err) {
        setParseError(err instanceof Error ? err.message : 'JSON 解析失败')
        // 不回调，保留父组件上一个合法值
      }
    },
    [onChange],
  )

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label
          className="flex items-center gap-1 text-xs text-on-surface-variant"
          title='JSON 对象格式；key 必须与策略代码中 g.xxx 引用的参数名一致；value 支持 int/float/str/bool。如 {"short_window": 5, "long_window": 10, "buy_ratio": 0.95}'
        >
          <span>参数（JSON）</span>
          <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
        </label>
        <div className="flex items-center gap-1 text-xs">
          {parseError ? (
            <span className="text-error flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              JSON 错误
            </span>
          ) : text.trim() ? (
            <span className="text-success flex items-center gap-1">
              <CheckCircle2 className="w-3 h-3" />
              合法
            </span>
          ) : (
            <span className="text-on-surface-variant">空</span>
          )}
        </div>
      </div>
      <textarea
        value={text}
        onChange={(e) => handleTextChange(e.target.value)}
        disabled={disabled}
        spellCheck={false}
        className="w-full h-40 p-3 rounded-md border border-outline-variant/30 bg-surface-container-lowest font-mono text-xs text-on-surface outline-none focus:border-primary transition-colors resize-y disabled:opacity-50"
        placeholder='{"short_window": 5, "long_window": 10}'
      />
      {parseError && (
        <div className="text-xs text-error flex items-start gap-1.5">
          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
          <span className="break-all">{parseError}</span>
        </div>
      )}
    </div>
  )
}
