/**
 * 策略编辑器（P1 MVP）。
 *
 * 功能：
 * - URL ?id=xxx 加载已有策略 / ?new=1 创建新策略
 * - Monaco 代码编辑器（Python 语法 + 项目暗色主题）
 * - 参数 JSON 编辑器（实时校验）
 * - 校验按钮：调 /api/strategy/validate，结果展示在右侧面板 + 编辑器红波浪线
 * - 试运行按钮：调 /api/strategy/{id}/dry-run（仅 active 策略可试运行）
 * - 保存按钮：新建或更新策略
 * - dryRun=1 URL 参数：加载完成后自动触发试运行
 *
 * 路由：/strategy-editor
 */

import { AppLayout } from '@/common/components'
import {
  AlertCircle,
  ArrowLeft,
  Brain,
  HelpCircle,
  Loader2,
  PlayCircle,
  Save,
  ShieldCheck,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  createStrategy,
  dryRunStrategy,
  getStrategy,
  updateStrategy,
  validateStrategyCode,
} from '../api/strategy'
import { CodeEditor } from '../components/CodeEditor'
import { ParamsEditor } from '../components/ParamsEditor'
import { ValidationPanel } from '../components/ValidationPanel'
import type {
  DryRunResult,
  Strategy,
  StrategyStatus,
  StrategyValidateResult,
} from '../types/strategy'

type Mode = 'loading' | 'create' | 'edit'

// 默认代码模板（新建策略时填入编辑器）
const DEFAULT_CODE_TEMPLATE = `# 策略说明：用 JoinQuant 风格 DSL 编写
# 必需钩子：initialize / handle_data
# 可选钩子：before_trading_start / control_risk
# 内置 API：g / context / data / log / get_history / order / order_value / order_target
# 允许 import：math / statistics / datetime / decimal / json / collections
# 注意：context.universe 已由引擎注入为 [stock_code]，无需 set_universe

def initialize(context):
    g.security = context.universe[0]  # 用引擎注入的 stock_code
    g.window = 5


def handle_data(context, data):
    security = context.universe[0] if context.universe else None
    if not security:
        return

    df = get_history(g.window + 5, '1d', 'close', security, fq='qfq', include=True)
    if not df or 'close' not in df or len(df['close']) < g.window:
        return

    closes = df['close']
    ma = sum(closes[-g.window:]) / g.window
    price = data[security]['close']

    if price > ma and context.portfolio.cash > 10000:
        order_value(security, context.portfolio.cash * 0.5)
        log.info('价格 %.2f 上穿 MA%d %.2f，买入' % (price, g.window, ma))
    elif price < ma:
        pos = context.portfolio.positions.get(security)
        if pos and pos.quantity > 0:
            order_target(security, 0)
            log.info('价格 %.2f 下穿 MA%d %.2f，卖出' % (price, g.window, ma))


def control_risk(context):
    pass
`

const selectClass =
  'h-9 px-3 rounded-md border border-outline bg-surface-container text-sm text-on-surface outline-none focus:border-primary transition-colors'

const inputClass =
  'h-9 px-3 rounded-md border border-outline bg-surface-container text-sm text-on-surface outline-none focus:border-primary transition-colors w-full'

export default function StrategyEditor() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const strategyIdParam = searchParams.get('id')
  const isNew = searchParams.get('new') === '1'
  const autoDryRun = searchParams.get('dryRun') === '1'

  const [mode, setMode] = useState<Mode>('loading')
  const [strategyId, setStrategyId] = useState<number | null>(
    strategyIdParam ? Number(strategyIdParam) : null,
  )

  // 表单字段
  const [code, setCode] = useState<string>(DEFAULT_CODE_TEMPLATE)
  const [name, setName] = useState<string>('')
  const [codeField, setCodeField] = useState<string>('') // 策略编码（DOUBLE_MA 风格）
  const [strategyType, setStrategyType] = useState<string>('trend')
  const [status, setStatus] = useState<StrategyStatus>('draft')
  const [version, setVersion] = useState<string>('1.0.0')
  const [description, setDescription] = useState<string>('')
  const [parameters, setParameters] = useState<Record<string, unknown> | null>(null)

  // 操作状态
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [dryRunning, setDryRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 结果
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null)

  // Toast
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; msg: string } | null>(
    null,
  )
  const showToast = useCallback((type: 'success' | 'error' | 'info', msg: string) => {
    setToast({ type, msg })
    window.setTimeout(() => setToast(null), 3000)
  }, [])

  // 自动触发 dryRun 标志（加载完成后消费）
  const autoDryRunTriggeredRef = useRef(false)

  // === 加载策略 ===
  useEffect(() => {
    if (isNew) {
      setMode('create')
      setName('')
      setCodeField('')
      setStrategyType('trend')
      setStatus('draft')
      setVersion('1.0.0')
      setDescription('')
      setParameters(null)
      setCode(DEFAULT_CODE_TEMPLATE)
      return
    }
    if (strategyId == null) {
      // 既没 id 也没 new，回到列表
      navigate('/strategies')
      return
    }

    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const s: Strategy = await getStrategy(strategyId)
        if (cancelled) return
        setName(s.name)
        setCodeField(s.code)
        setStrategyType(s.strategy_type)
        setStatus(s.status)
        setVersion(s.version)
        setDescription(s.description ?? '')
        setParameters(s.parameters ?? null)
        setCode(s.code_content ?? DEFAULT_CODE_TEMPLATE)
        setMode('edit')
      } catch (err) {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : '加载策略失败'
        setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [strategyId, isNew, navigate])

  // === 加载完成后自动 dry-run ===
  useEffect(() => {
    if (!autoDryRun || autoDryRunTriggeredRef.current) return
    if (mode !== 'edit' || strategyId == null) return
    if (status !== 'active') {
      showToast('info', '仅 active 策略可试运行，请先启用')
      autoDryRunTriggeredRef.current = true
      return
    }
    autoDryRunTriggeredRef.current = true
    void handleDryRun()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoDryRun, mode, strategyId, status])

  // === 校验 ===
  const handleValidate = useCallback(async () => {
    setValidating(true)
    setError(null)
    try {
      const result = await validateStrategyCode({ code_content: code })
      setValidation(result)
      if (result.valid) {
        showToast('success', '校验通过')
      } else {
        showToast('error', `发现 ${result.errors.length} 个错误`)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '校验请求失败'
      setError(msg)
    } finally {
      setValidating(false)
    }
  }, [code, showToast])

  // === 试运行 ===
  const handleDryRun = useCallback(async () => {
    if (strategyId == null) {
      showToast('error', '请先保存策略')
      return
    }
    if (status !== 'active') {
      showToast('error', '仅 active 策略可试运行')
      return
    }
    setDryRunning(true)
    setError(null)
    setDryRunResult(null)
    try {
      const result = await dryRunStrategy(strategyId, {
        stock_code: '000001.SZ',
        start_date: '2024-01-01',
        end_date: '2024-03-01',
        parameters: parameters ?? undefined,
        max_bars: 60,
      })
      setDryRunResult(result)
      showToast(
        'success',
        `试运行完成：${result.total_bars} bar，收益 ${result.total_return_pct > 0 ? '+' : ''}${result.total_return_pct}%`,
      )
    } catch (err) {
      const msg = err instanceof Error ? err.message : '试运行失败'
      setError(msg)
    } finally {
      setDryRunning(false)
    }
  }, [strategyId, status, parameters, showToast])

  // === 保存 ===
  const handleSave = useCallback(async () => {
    if (!name.trim()) {
      showToast('error', '策略名称不能为空')
      return
    }
    if (!codeField.trim()) {
      showToast('error', '策略编码不能为空（如 DOUBLE_MA）')
      return
    }
    if (!/^[A-Z][A-Z0-9_]{2,63}$/.test(codeField)) {
      showToast('error', '编码格式：大写字母开头+数字/下划线，3~64 字符')
      return
    }

    setSaving(true)
    setError(null)
    try {
      const payload = {
        code: codeField,
        name: name.trim(),
        strategy_type: strategyType,
        description: description.trim() || undefined,
        status,
        version,
        code_content: code,
        parameters: parameters ?? undefined,
      }
      if (mode === 'create') {
        const created = await createStrategy(payload)
        setStrategyId(created.id)
        setMode('edit')
        showToast('success', `策略已创建（id=${created.id}）`)
        // 用 navigate 替代 window.history.replaceState，让 React Router 感知 URL 变化
        // 触发 useEffect 重新加载策略数据并回填表单
        navigate(`/strategy-editor?id=${created.id}`, { replace: true })
      } else if (mode === 'edit' && strategyId != null) {
        await updateStrategy(strategyId, payload)
        showToast('success', '策略已更新')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '保存失败'
      setError(msg)
    } finally {
      setSaving(false)
    }
  }, [
    name,
    codeField,
    strategyType,
    description,
    status,
    version,
    code,
    parameters,
    mode,
    strategyId,
    showToast,
  ])

  // 计算派生值
  const charCount = useMemo(() => code.length, [code])
  const lineCount = useMemo(() => code.split('\n').length, [code])

  return (
    <AppLayout>
      <div className="min-h-full p-6 space-y-4">
        {/* 顶部：返回 + 标题 + 操作 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/strategies')}
              className="p-2 rounded-md hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
              title="返回策略列表"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <div className="w-9 h-9 rounded-lg bg-primary/20 flex items-center justify-center">
              <Brain className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-on-surface">
                {mode === 'create' ? '新建策略' : name || '未命名策略'}
                {mode === 'edit' && (
                  <span
                    className={`ml-2 px-1.5 py-0.5 text-xs rounded-full border ${
                      status === 'active'
                        ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                        : status === 'archived'
                          ? 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30'
                          : 'bg-slate-500/20 text-slate-300 border-slate-500/30'
                    }`}
                  >
                    {status === 'active' ? '已启用' : status === 'archived' ? '已归档' : '草稿'}
                  </span>
                )}
              </h1>
              <p className="text-xs text-on-surface-variant">
                {mode === 'edit' && strategyId != null && (
                  <>ID {strategyId} · {lineCount} 行 · {charCount} 字符 · </>
                )}
                {mode === 'create' ? '新建模式' : '编辑模式'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleValidate()}
              disabled={validating}
              className="px-3 h-9 rounded-md border border-outline bg-surface-container hover:bg-surface-container-highest text-on-surface text-sm transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              {validating ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
              校验
            </button>
            <button
              type="button"
              onClick={() => void handleDryRun()}
              disabled={dryRunning || mode !== 'edit' || status !== 'active'}
              className="px-3 h-9 rounded-md border border-outline bg-surface-container hover:bg-surface-container-highest text-on-surface text-sm transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              title={status !== 'active' ? '仅 active 策略可试运行' : '基于 mock V 形趋势数据跑 60 bar'}
            >
              {dryRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />}
              试运行
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="px-4 h-9 rounded-md bg-primary hover:bg-primary-container text-primary-foreground text-sm font-medium transition-colors flex items-center gap-1.5 disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {mode === 'create' ? '创建' : '保存'}
            </button>
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="p-3 rounded-md border border-error/30 bg-error/10 text-error flex items-center gap-2 text-sm">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {/* 主体：左编辑器 + 右侧栏 */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 左侧：基础信息 + 代码 */}
          <div className="lg:col-span-2 space-y-4">
            {/* 基础信息表单 */}
            <div className="bg-surface-container-high rounded-lg border border-outline-variant/20 p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="1-128 字符，中英文均可；保存后可修改"
                  >
                    <span>策略名称 *</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="如：双均线交叉"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="大写字母开头+数字/下划线，3-64 字符；保存后不可修改。如 DOUBLE_MA、RSI_REVERSAL"
                  >
                    <span>策略编码 *</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <input
                    type="text"
                    value={codeField}
                    onChange={(e) => setCodeField(e.target.value.toUpperCase())}
                    placeholder="如：DOUBLE_MA"
                    className={`${inputClass} font-mono`}
                    disabled={mode === 'edit'}
                  />
                </div>
                <div>
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="trend=趋势跟随 / mean_reversion=均值回归 / arbitrage=套利 / sentiment=情绪驱动"
                  >
                    <span>类型</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <select
                    value={strategyType}
                    onChange={(e) => setStrategyType(e.target.value)}
                    className={selectClass}
                  >
                    <option value="trend">趋势跟随</option>
                    <option value="mean_reversion">均值回归</option>
                    <option value="arbitrage">套利</option>
                    <option value="sentiment">情绪驱动</option>
                  </select>
                </div>
                <div>
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="draft=草稿 / active=已启用（仅 active 可试运行与实盘执行）/ archived=已归档"
                  >
                    <span>状态</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value as StrategyStatus)}
                    className={selectClass}
                  >
                    <option value="draft">草稿</option>
                    <option value="active">已启用</option>
                    <option value="archived">已归档</option>
                  </select>
                </div>
                <div>
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="语义化版本号，格式如 1.0.0（MAJOR.MINOR.PATCH）；新建版本快照时建议递增"
                  >
                    <span>版本号</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <input
                    type="text"
                    value={version}
                    onChange={(e) => setVersion(e.target.value)}
                    placeholder="1.0.0"
                    className={`${inputClass} font-mono`}
                  />
                </div>
                <div className="col-span-2">
                  <label
                    className="flex items-center gap-1 text-xs text-on-surface-variant mb-1"
                    title="策略的一句话说明，便于在列表识别；可留空"
                  >
                    <span>描述</span>
                    <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                  </label>
                  <input
                    type="text"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="简短说明策略逻辑"
                    className={inputClass}
                  />
                </div>
              </div>
            </div>

            {/* Monaco 编辑器 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label
                  className="flex items-center gap-1 text-xs text-on-surface-variant"
                  title="Python DSL 语法；必需 initialize(context) 与 handle_data(context, data) 两个钩子；允许 import: math/statistics/datetime/decimal/json/collections；可用 API: g/log/order/order_value/order_target/order_target_value/get_history/set_universe/get_current_data"
                >
                  <span>策略代码（Python DSL）</span>
                  <HelpCircle className="w-3 h-3 cursor-help opacity-60" />
                </label>
                <div className="text-xs text-on-surface-variant font-mono-num">
                  {lineCount} 行 · {charCount} 字符
                </div>
              </div>
              {loading ? (
                <div className="h-[480px] flex items-center justify-center bg-surface-container-lowest border border-outline-variant/20 rounded-md text-on-surface-variant text-sm">
                  <Loader2 className="w-5 h-5 animate-spin mr-2" />
                  加载策略代码...
                </div>
              ) : (
                <CodeEditor
                  value={code}
                  onChange={setCode}
                  errors={validation?.errors ?? []}
                  minHeight={520}
                />
              )}
            </div>
          </div>

          {/* 右侧：参数 + 校验 + dry-run 结果 */}
          <div className="space-y-4">
            {/* 参数 */}
            <div className="bg-surface-container-high rounded-lg border border-outline-variant/20 p-4">
              <ParamsEditor value={parameters} onChange={setParameters} />
            </div>

            {/* 校验结果 */}
            <div className="bg-surface-container-high rounded-lg border border-outline-variant/20 p-4 space-y-2">
              <div className="flex items-center gap-1.5 text-xs text-on-surface-variant">
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>校验结果</span>
              </div>
              <ValidationPanel result={validation} loading={validating} />
            </div>

            {/* 试运行结果 */}
            <div className="bg-surface-container-high rounded-lg border border-outline-variant/20 p-4 space-y-3">
              <div className="flex items-center gap-1.5 text-xs text-on-surface-variant">
                <PlayCircle className="w-3.5 h-3.5" />
                <span>试运行结果</span>
              </div>
              <DryRunResultView result={dryRunResult} loading={dryRunning} />
            </div>
          </div>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className="fixed bottom-6 right-6 z-50 px-4 py-3 rounded-md shadow-float text-sm flex items-center gap-2"
          style={{
            backgroundColor:
              toast.type === 'success'
                ? 'rgba(34,197,94,0.15)'
                : toast.type === 'error'
                  ? 'rgba(239,68,68,0.15)'
                  : 'rgba(59,130,246,0.15)',
            color:
              toast.type === 'success' ? '#22c55e' : toast.type === 'error' ? '#ef4444' : '#3b82f6',
            border: '1px solid currentColor',
          }}
        >
          {toast.msg}
        </div>
      )}
    </AppLayout>
  )
}

// ============================================================
// 试运行结果视图
// ============================================================

function DryRunResultView({
  result,
  loading,
}: {
  result: DryRunResult | null
  loading: boolean
}) {
  if (loading) {
    return (
      <div className="text-xs text-on-surface-variant flex items-center gap-2">
        <Loader2 className="w-3 h-3 animate-spin" />
        试运行中（基于 mock 数据）...
      </div>
    )
  }
  if (!result) {
    return (
      <div className="text-xs text-on-surface-variant">
        点击"试运行"用 mock V 形趋势数据跑 60 个 bar，验证策略能否产生订单。
      </div>
    )
  }

  const profit = result.total_return_pct >= 0
  const orders = result.bars.reduce((sum, b) => sum + b.orders_count, 0)

  return (
    <div className="space-y-3">
      {/* 核心指标 */}
      <div className="grid grid-cols-2 gap-2">
        <MetricCell label="总收益" value={`${profit ? '+' : ''}${result.total_return_pct}%`} profit={profit} />
        <MetricCell
          label="期末资产"
          value={result.final_capital.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
        />
        <MetricCell label="Bar 数" value={`${result.total_bars}`} />
        <MetricCell label="订单数" value={`${orders}`} />
      </div>

      {/* 简化资金曲线（最近 N 个 bar） */}
      {result.bars.length > 0 && (
        <Sparkline points={result.bars.map((b) => b.total_assets)} positive={profit} />
      )}

      {/* 详情链接 */}
      <div className="text-xs text-on-surface-variant">
        Session: <code className="font-mono">{result.session_id}</code>
      </div>
    </div>
  )
}

function MetricCell({
  label,
  value,
  profit,
}: {
  label: string
  value: string
  profit?: boolean
}) {
  const valueColor = profit === undefined ? 'text-on-surface' : profit ? 'text-up' : 'text-down'
  return (
    <div className="p-2 rounded-md bg-surface-container border border-outline-variant/20">
      <div className="text-xs text-on-surface-variant">{label}</div>
      <div className={`text-sm font-medium font-mono-num ${valueColor}`}>{value}</div>
    </div>
  )
}

/** 简化资金曲线（SVG path），不依赖 ECharts 避免打包膨胀 */
function Sparkline({ points, positive }: { points: number[]; positive: boolean }) {
  if (points.length < 2) return null

  const width = 240
  const height = 50
  const padding = 4

  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1

  const stepX = (width - padding * 2) / (points.length - 1)
  const pathData = points
    .map((p, i) => {
      const x = padding + i * stepX
      const y = padding + (height - padding * 2) * (1 - (p - min) / range)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const strokeColor = positive ? '#ef4444' : '#22c55e' // 中国惯例：红涨绿跌

  return (
    <div>
      <div className="text-xs text-on-surface-variant mb-1">资金曲线</div>
      <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        <path
          d={pathData}
          stroke={strokeColor}
          strokeWidth={1.5}
          fill="none"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  )
}
