import { AppLayout } from '@/common/components'
import request from '@/common/utils/request'
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  RefreshCcw,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { StatusCards } from '../components/StatusCards'
import { ExecutionList } from '../components/ExecutionList'
import type {
  Execution,
  ExecutionStatus,
  ExecutionSignal,
  RiskAlert,
  ExecutionLog,
  RiskRule,
  StartExecutionRequest,
} from '../types/execution'

type TabKey = 'executions' | 'signals' | 'risk' | 'logs'

const apiTimeout = 240000
const inputClass =
  'h-10 w-full rounded-md border border-outline bg-surface-container px-3 text-sm text-on-surface outline-none transition-colors placeholder:text-on-surface-variant focus:border-primary'

export default function Execution() {
  const [activeTab, setActiveTab] = useState<TabKey>('executions')
  const [status, setStatus] = useState<ExecutionStatus>({
    running_count: 0,
    paused_count: 0,
    stopped_count: 0,
    today_pnl: 0,
    active_alerts: 0,
    total_signals_today: 0,
  })
  const [executions, setExecutions] = useState<Execution[]>([])
  const [signals, setSignals] = useState<ExecutionSignal[]>([])
  const [alerts, setAlerts] = useState<RiskAlert[]>([])
  const [logs, setLogs] = useState<ExecutionLog[]>([])
  const [riskRules, setRiskRules] = useState<RiskRule[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [showStartModal, setShowStartModal] = useState(false)
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null)
  const [startForm, setStartForm] = useState<StartExecutionRequest>({
    strategy_id: 0,
    account_id: 0,
    params: { symbol: '000001.SZ', timeframe: '1d' },
  })
  const [strategies, setStrategies] = useState<Array<{ id: number; name: string }>>([])
  const [accounts, setAccounts] = useState<
    Array<{ id: number; account_code: string; account_name: string; account_type?: string }>
  >([])

  const busyText = useMemo(
    () =>
      ({
        status: '加载状态',
        executions: '加载执行列表',
        signals: '加载信号',
        alerts: '加载告警',
        logs: '加载日志',
        start: '启动策略',
        stop: '停止策略',
        pause: '暂停策略',
        resume: '恢复策略',
        signal: '触发策略一轮',
        acknowledge: '确认告警',
        tick: '执行一轮',
      })[busy || ''] || '',
    [busy]
  )

  async function apiCall<T>(label: string, work: () => Promise<T>): Promise<T | null> {
    setBusy(label)
    setError('')
    setMessage('')
    try {
      const data = await work()
      setMessage('')
      return data
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: { message?: string } } }; message?: string }
      const detail = error?.response?.data?.detail
      setError(detail?.message || error?.message || '请求失败')
      return null
    } finally {
      setBusy(null)
    }
  }

  async function refreshStatus() {
    try {
      const data = await request.get<any>('/execution/status', { timeout: apiTimeout })
      if (data) setStatus(data)
    } catch {
      // 使用默认值
    }
  }

  async function refreshExecutions() {
    try {
      const data = await request.get<any>('/execution/list', { timeout: apiTimeout })
      if (data?.items) setExecutions(data.items)
    } catch {
      setExecutions([])
    }
  }

  async function refreshSignals() {
    try {
      const data = await request.get<any>('/execution/signals', { timeout: apiTimeout })
      if (data) setSignals(Array.isArray(data) ? data : [])
    } catch {
      setSignals([])
    }
  }

  async function refreshAlerts() {
    try {
      const data = await request.get<any>('/execution/risk-alerts/active', { timeout: apiTimeout })
      if (data) setAlerts(Array.isArray(data) ? data : [])
    } catch {
      setAlerts([])
    }
  }

  async function refreshLogs() {
    if (selectedExecutionId) {
      try {
        const data = await request.get<any>(`/execution/${selectedExecutionId}/logs`, { timeout: apiTimeout })
        if (data?.items) setLogs(data.items)
      } catch {
        setLogs([])
      }
    }
  }

  async function refreshStrategies() {
    try {
      const data = await request.get<any>('/strategy/list', { timeout: apiTimeout })
      const list = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []
      setStrategies(
        list
          .filter((s: { status?: string }) => !s.status || s.status === 'active')
          .map((s: { id: number; name: string }) => ({ id: s.id, name: s.name }))
      )
    } catch (e) {
      setStrategies([])
      setError((e as Error).message || '加载策略列表失败')
    }
  }

  async function refreshAccounts() {
    try {
      const data = await request.get<any>('/account/accounts', { timeout: apiTimeout })
      const list = Array.isArray(data) ? data : []
      setAccounts(
        list
          .filter((a: { account_type?: string; status?: string }) =>
            (a.account_type === 'paper' || a.account_type === 'live') && a.status !== 'archived'
          )
          .map((a: { id: number; account_code: string; account_name: string; account_type?: string }) => ({
            id: a.id,
            account_code: a.account_code,
            account_name: a.account_name,
            account_type: a.account_type,
          }))
      )
    } catch (e) {
      setAccounts([])
      setError((e as Error).message || '加载账户列表失败')
    }
  }

  async function refreshRiskRules() {
    try {
      const data = await request.get<any>('/execution/risk-rules', { timeout: apiTimeout })
      if (data) setRiskRules(Array.isArray(data) ? data : data?.items || [])
    } catch {
      setRiskRules([])
    }
  }

  const bootstrap = useCallback(async () => {
    await Promise.all([
      refreshStatus(),
      refreshExecutions(),
      refreshStrategies(),
      refreshAccounts(),
      refreshRiskRules(),
    ])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => {
      void bootstrap()
    }, 0)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function startExecution(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const symbol = String(startForm.params?.symbol || '').trim()
    if (!startForm.strategy_id || !startForm.account_id) {
      setError('请选择策略和账户')
      return
    }
    if (!symbol) {
      setError('请填写交易标的（如 000001.SZ）')
      return
    }
    try {
      setBusy('start')
      setError('')
      await request.post(
        '/execution/start',
        {
          strategy_id: startForm.strategy_id,
          account_id: Number(startForm.account_id),
          params: {
            ...(startForm.params || {}),
            symbol,
            timeframe: startForm.params?.timeframe || '1d',
          },
        },
        { timeout: apiTimeout }
      )
      setShowStartModal(false)
      setStartForm({
        strategy_id: 0,
        account_id: 0,
        params: { symbol: '000001.SZ', timeframe: '1d' },
      })
      setMessage('策略已启动，已接入账户交易链路')
      await Promise.all([refreshStatus(), refreshExecutions(), refreshSignals()])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function stopExecution(id: number) {
    try {
      await request.post(`/execution/${id}/stop`, {}, { timeout: apiTimeout })
      await Promise.all([refreshStatus(), refreshExecutions()])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function pauseExecution(id: number) {
    try {
      await request.post(`/execution/${id}/pause`, {}, { timeout: apiTimeout })
      await Promise.all([refreshStatus(), refreshExecutions()])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function resumeExecution(id: number) {
    try {
      await request.post(`/execution/${id}/resume`, {}, { timeout: apiTimeout })
      await Promise.all([refreshStatus(), refreshExecutions()])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function forceTick(id: number) {
    const data = await apiCall<Record<string, unknown>>('tick', () =>
      request.post(`/execution/${id}/tick`, {}, { timeout: apiTimeout })
    )
    if (data) {
      setMessage(`已触发一轮：信号意图 ${(data as { intents?: number }).intents ?? 0}，订单 ${(data as { orders?: unknown[] }).orders?.length ?? 0}`)
      await Promise.all([refreshStatus(), refreshExecutions(), refreshSignals(), refreshLogs()])
    }
  }

  async function viewExecutionDetail(id: number) {
    setSelectedExecutionId(id)
    await refreshLogs()
    setActiveTab('logs')
  }

  async function acknowledgeAlert(id: number) {
    const data = await apiCall<RiskAlert>('acknowledge', () =>
      request.post(`/execution/risk-alerts/${id}/acknowledge`, {}, { timeout: apiTimeout })
    )
    if (data) {
      await Promise.all([refreshStatus(), refreshAlerts()])
    }
  }

  async function toggleRiskRule(id: number, enabled: boolean) {
    const data = await apiCall<RiskRule>('toggle', () =>
      request.post(
        `/execution/risk-rules/${id}/${enabled ? 'disable' : 'enable'}`,
        {},
        { timeout: apiTimeout }
      )
    )
    if (data) {
      await refreshRiskRules()
    }
  }

  async function refreshActiveTab() {
    if (activeTab === 'executions') await refreshExecutions()
    if (activeTab === 'signals') await refreshSignals()
    if (activeTab === 'risk') await Promise.all([refreshAlerts(), refreshRiskRules()])
    if (activeTab === 'logs') {
      if (selectedExecutionId) {
        await refreshLogs()
      } else {
        setMessage('请先在执行列表中选择一个执行实例查看日志')
      }
    }
    await refreshStatus()
  }

  return (
    <AppLayout>
      <div className="space-y-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-on-surface">执行监控</h1>
            <p className="mt-1 text-sm text-on-surface-variant">
              策略引擎 → 风控 → 账户交易（paper 模拟 / live 同花顺）闭环监控。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ActionButton
              label="启动策略"
              icon={<Play className="size-4" />}
              onClick={() => setShowStartModal(true)}
            />
            <ActionButton
              label="刷新"
              icon={<RefreshCcw className="size-4" />}
              onClick={() => void refreshActiveTab()}
            />
          </div>
        </div>

        {(message || error || busy) && <Notice busy={busyText} message={message} error={error} />}

        <StatusCards status={status} />

        <section className="rounded-lg bg-surface-container-high p-4 shadow-card">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-1 rounded-md bg-surface-container p-1">
              <TabButton active={activeTab === 'executions'} label="执行列表" onClick={() => setActiveTab('executions')} />
              <TabButton active={activeTab === 'signals'} label="交易信号" onClick={() => { setActiveTab('signals'); void refreshSignals(); }} />
              <TabButton active={activeTab === 'risk'} label="风控中心" onClick={() => { setActiveTab('risk'); void Promise.all([refreshAlerts(), refreshRiskRules()]); }} />
              <TabButton active={activeTab === 'logs'} label="执行日志" onClick={() => setActiveTab('logs')} />
            </div>
          </div>

          {activeTab === 'executions' && (
            <ExecutionList
              executions={executions}
              onStart={() => setShowStartModal(true)}
              onStop={stopExecution}
              onPause={pauseExecution}
              onResume={resumeExecution}
              onGenerateSignal={forceTick}
              onViewDetail={viewExecutionDetail}
            />
          )}

          {activeTab === 'signals' && <SignalsTable signals={signals} />}

          {activeTab === 'risk' && (
            <div className="space-y-6">
              <div>
                <h3 className="mb-3 text-sm font-semibold text-on-surface">活跃告警</h3>
                <AlertsList alerts={alerts} onAcknowledge={acknowledgeAlert} />
              </div>
              <div>
                <h3 className="mb-3 text-sm font-semibold text-on-surface">风控规则</h3>
                <RiskRulesTable rules={riskRules} onToggle={toggleRiskRule} />
              </div>
            </div>
          )}

          {activeTab === 'logs' && (
            <div>
              {selectedExecutionId ? (
                <LogsTable logs={logs} />
              ) : (
                <div className="flex h-64 items-center justify-center rounded-md border border-outline-variant text-sm text-on-surface-variant">
                  请先在"执行列表"中点击查看详情以查看对应执行实例的日志
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {showStartModal && (
        <StartExecutionModal
          strategies={strategies}
          accounts={accounts}
          form={startForm}
          setForm={setStartForm}
          busy={busy === 'start'}
          onSubmit={startExecution}
          onCancel={() => setShowStartModal(false)}
        />
      )}
    </AppLayout>
  )
}

function Notice({ busy, message, error }: { busy: string; message: string; error: string }) {
  return (
    <div className="rounded-md border border-outline bg-surface-container px-4 py-3 text-sm">
      {busy && (
        <div className="flex items-center gap-2 text-primary">
          <Loader2 className="size-4 animate-spin" />
          {busy}中
        </div>
      )}
      {message && !busy && (
        <div className="flex items-center gap-2 text-success">
          <CheckCircle2 className="size-4" />
          {message}
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 text-error">
          <AlertTriangle className="size-4" />
          {error}
        </div>
      )}
    </div>
  )
}

function ActionButton({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string
  icon?: React.ReactNode
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center gap-2 rounded-md border border-outline bg-surface-container px-3 py-2 text-sm text-on-surface hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-60"
    >
      {icon}
      {label}
    </button>
  )
}

function TabButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-sm px-3 py-2 text-sm transition-colors ${
        active ? 'bg-primary/15 text-primary' : 'text-on-surface-variant hover:bg-surface-container-high'
      }`}
    >
      {label}
    </button>
  )
}

function SignalsTable({ signals }: { signals: ExecutionSignal[] }) {
  if (!signals.length) {
    return (
      <div className="flex h-64 items-center justify-center rounded-md border border-outline-variant text-sm text-on-surface-variant">
        暂无交易信号
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[960px] text-sm">
        <thead>
          <tr className="border-b border-outline text-xs text-on-surface-variant">
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">时间</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">代码</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">名称</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">方向</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">信号价</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">数量</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">风控</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">订单状态</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">盈亏</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant/40">
          {signals.map((signal) => (
            <tr key={signal.id} className="hover:bg-surface-container-highest">
              <td className="whitespace-nowrap px-3 py-2 text-on-surface-variant">{signal.created_at}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono-num">{signal.symbol}</td>
              <td className="whitespace-nowrap px-3 py-2">{signal.symbol_name || '-'}</td>
              <td className="whitespace-nowrap px-3 py-2">
                <span className={signal.direction === 'buy' ? 'text-up' : 'text-down'}>
                  {signal.direction === 'buy' ? '买入' : '卖出'}
                </span>
              </td>
              <td className="whitespace-nowrap px-3 py-2 font-mono-num">{signal.signal_price.toLocaleString()}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono-num">{signal.quantity.toLocaleString()}</td>
              <td className="whitespace-nowrap px-3 py-2">
                {signal.risk_passed ? (
                  <span className="text-success">通过</span>
                ) : (
                  <span className="text-error" title={signal.risk_reason}>
                    拒绝
                  </span>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2">{signal.order_status || '-'}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono-num">
                {signal.pnl !== undefined ? (
                  <span className={signal.pnl >= 0 ? 'text-up' : 'text-down'}>
                    {signal.pnl >= 0 ? '+' : ''}¥{signal.pnl.toLocaleString()}
                  </span>
                ) : (
                  '-'
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AlertsList({
  alerts,
  onAcknowledge,
}: {
  alerts: RiskAlert[]
  onAcknowledge: (id: number) => void
}) {
  if (!alerts.length) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-outline-variant text-sm text-on-surface-variant">
        暂无活跃告警
      </div>
    )
  }

  const severityClass = (severity: string) => {
    if (severity === 'critical') return 'border-error/50 bg-error/10'
    if (severity === 'warning') return 'border-warning/50 bg-warning/10'
    return 'border-primary/50 bg-primary/10'
  }

  const severityText = (severity: string) => {
    if (severity === 'critical') return '严重'
    if (severity === 'warning') return '警告'
    return '提示'
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert) => (
        <div
          key={alert.id}
          className={`rounded-lg border p-4 ${severityClass(alert.severity)}`}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <AlertTriangle className="size-4 text-on-surface" />
                <span className="font-medium text-on-surface">{alert.title}</span>
                <span className="rounded-sm px-2 py-0.5 text-xs bg-surface-container text-on-surface-variant">
                  {severityText(alert.severity)}
                </span>
              </div>
              <p className="text-sm text-on-surface-variant">{alert.message}</p>
              <p className="text-xs text-on-surface-variant mt-2">{alert.created_at}</p>
            </div>
            {!alert.acknowledged && (
              <button
                type="button"
                onClick={() => onAcknowledge(alert.id)}
                className="shrink-0 rounded-md border border-outline bg-surface-container px-3 py-1.5 text-xs text-on-surface hover:bg-surface-container-highest"
              >
                确认
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function RiskRulesTable({
  rules,
  onToggle,
}: {
  rules: RiskRule[]
  onToggle: (id: number, enabled: boolean) => void
}) {
  if (!rules.length) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-outline-variant text-sm text-on-surface-variant">
        暂无风控规则
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-outline text-xs text-on-surface-variant">
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">规则名称</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">类型</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">阈值</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">说明</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">状态</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant/40">
          {rules.map((rule) => (
            <tr key={rule.id} className="hover:bg-surface-container-highest">
              <td className="whitespace-nowrap px-3 py-2 font-medium">{rule.rule_name}</td>
              <td className="whitespace-nowrap px-3 py-2 text-on-surface-variant">{rule.rule_type}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono-num">
                {Object.entries(rule.params || {})
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(', ')}
              </td>
              <td className="px-3 py-2 text-on-surface-variant">{rule.description || '-'}</td>
              <td className="whitespace-nowrap px-3 py-2">
                {rule.enabled ? (
                  <span className="text-success">已启用</span>
                ) : (
                  <span className="text-on-surface-variant">已禁用</span>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-2">
                <button
                  type="button"
                  onClick={() => onToggle(rule.id, rule.enabled)}
                  className={`inline-flex items-center gap-1 rounded-sm px-2 py-1 text-xs ${
                    rule.enabled
                      ? 'text-warning hover:bg-warning/10'
                      : 'text-success hover:bg-success/10'
                  }`}
                >
                  {rule.enabled ? '禁用' : '启用'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LogsTable({ logs }: { logs: ExecutionLog[] }) {
  if (!logs.length) {
    return (
      <div className="flex h-64 items-center justify-center rounded-md border border-outline-variant text-sm text-on-surface-variant">
        暂无执行日志
      </div>
    )
  }

  const levelClass = (level: string) => {
    if (level === 'error') return 'text-error'
    if (level === 'warning') return 'text-warning'
    if (level === 'success') return 'text-success'
    return 'text-on-surface-variant'
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-outline text-xs text-on-surface-variant">
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">时间</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">级别</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">分类</th>
            <th className="whitespace-nowrap px-3 py-2 text-left font-medium">消息</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant/40">
          {logs.map((log) => (
            <tr key={log.id} className="hover:bg-surface-container-highest">
              <td className="whitespace-nowrap px-3 py-2 text-on-surface-variant">{log.created_at}</td>
              <td className="whitespace-nowrap px-3 py-2">
                <span className={levelClass(log.level)}>{log.level.toUpperCase()}</span>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-on-surface-variant">{log.category}</td>
              <td className="px-3 py-2">{log.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StartExecutionModal({
  strategies,
  accounts,
  form,
  setForm,
  busy,
  onSubmit,
  onCancel,
}: {
  strategies: Array<{ id: number; name: string }>
  accounts: Array<{ id: number; account_code: string; account_name: string; account_type?: string }>
  form: StartExecutionRequest
  setForm: React.Dispatch<React.SetStateAction<StartExecutionRequest>>
  busy: boolean
  onSubmit: (e: React.FormEvent<HTMLFormElement>) => void
  onCancel: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 px-4">
      <div className="w-full max-w-md rounded-lg border border-outline bg-surface-container-high p-5 shadow-card">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-on-surface">启动策略执行</h3>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-sm p-1 text-on-surface-variant hover:bg-surface-container"
          >
            <X className="size-4" />
          </button>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <Field label="选择策略（须为 active）">
            <select
              value={form.strategy_id || ''}
              onChange={(e) => setForm({ ...form, strategy_id: Number(e.target.value) })}
              className={inputClass}
              required
            >
              <option value="">请选择策略</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="交易账户（paper 模拟 / live 实盘）">
            <select
              value={form.account_id || ''}
              onChange={(e) => setForm({ ...form, account_id: Number(e.target.value) })}
              className={inputClass}
              required
            >
              <option value="">请选择账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  [{a.account_type || '?'}] {a.account_name} ({a.account_code})
                </option>
              ))}
            </select>
          </Field>
          <Field label="交易标的">
            <input
              value={String(form.params?.symbol || '')}
              onChange={(e) =>
                setForm({
                  ...form,
                  params: { ...(form.params || {}), symbol: e.target.value.trim() },
                })
              }
              className={inputClass}
              placeholder="例如 000001.SZ"
              required
            />
          </Field>
          <p className="text-xs text-on-surface-variant">
            启动后将加载策略代码，按最新 K 线跑 handle_data；有信号则经风控后写入对应账户订单（模拟或同花顺实盘）。
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-outline px-4 py-2 text-sm text-on-surface hover:bg-surface-container-highest"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              启动
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-on-surface-variant">{label}</span>
      {children}
    </label>
  )
}
