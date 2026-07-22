import { Play, Pause, Square, MoreHorizontal, TrendingUp, Clock } from 'lucide-react'
import type { Execution } from '../types/execution'

interface ExecutionListProps {
  executions: Execution[]
  onStart?: () => void
  onStop: (id: number) => void
  onPause: (id: number) => void
  onResume: (id: number) => void
  onGenerateSignal: (id: number) => void
  onViewDetail: (id: number) => void
}

export function ExecutionList({
  executions,
  onStart,
  onStop,
  onPause,
  onResume,
  onGenerateSignal,
  onViewDetail,
}: ExecutionListProps) {
  const getStatusBadge = (status: Execution['status']) => {
    const styles = {
      running: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
      paused: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
      stopped: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
    }
    const labels = {
      running: '运行中',
      paused: '已暂停',
      stopped: '已停止',
    }
    return (
      <span className={`px-2 py-0.5 text-xs rounded-full border ${styles[status]}`}>
        {labels[status]}
      </span>
    )
  }

  return (
    <div className="bg-surface-container-high rounded-lg border border-outline-variant/20">
      <div className="p-4 border-b border-outline-variant/20 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-on-surface">策略执行实例</h3>
        {onStart && (
          <button
            onClick={onStart}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors flex items-center gap-2"
          >
            <Play className="w-4 h-4" />
            启动策略
          </button>
        )}
      </div>

      <div className="divide-y divide-outline-variant/10">
        {executions.length === 0 ? (
          <div className="p-8 text-center text-on-surface-variant">
            <Clock className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p>暂无执行实例</p>
            <p className="text-sm mt-1">点击"启动策略"开始运行</p>
          </div>
        ) : (
          executions.map((execution) => (
            <div
              key={execution.id}
              className="p-4 hover:bg-surface-container/50 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-2">
                    <h4 className="font-medium text-on-surface">{execution.strategy_name}</h4>
                    {getStatusBadge(execution.status)}
                  </div>
                  <div className="text-sm text-on-surface-variant space-y-1">
                    <div className="flex items-center gap-4">
                      <span>策略ID: {execution.strategy_id}</span>
                      <span>账户: {execution.account_id}</span>
                    </div>
                    <div className="flex items-center gap-4">
                      <span>
                        开始时间: {new Date(execution.start_time).toLocaleString('zh-CN')}
                      </span>
                      {execution.end_time && (
                        <span>
                          结束时间: {new Date(execution.end_time).toLocaleString('zh-CN')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-6 mt-3 text-sm">
                    <div>
                      <span className="text-on-surface-variant">信号: </span>
                      <span className="font-mono-num text-on-surface">{execution.total_signals}</span>
                    </div>
                    <div>
                      <span className="text-on-surface-variant">订单: </span>
                      <span className="font-mono-num text-on-surface">{execution.total_orders}</span>
                    </div>
                    <div>
                      <span className="text-on-surface-variant">盈亏: </span>
                      <span
                        className={`font-mono-num font-medium ${
                          execution.total_pnl >= 0 ? 'text-up' : 'text-down'
                        }`}
                      >
                        {execution.total_pnl >= 0 ? '+' : ''}¥{execution.total_pnl.toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {execution.status === 'running' && (
                    <>
                      <button
                        onClick={() => onGenerateSignal(execution.id)}
                        className="p-2 text-blue-400 hover:bg-blue-500/10 rounded-lg transition-colors"
                        title="立即执行一轮（策略→风控→下单）"
                      >
                        <TrendingUp className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onPause(execution.id)}
                        className="p-2 text-amber-400 hover:bg-amber-500/10 rounded-lg transition-colors"
                        title="暂停"
                      >
                        <Pause className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onStop(execution.id)}
                        className="p-2 text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                        title="停止"
                      >
                        <Square className="w-4 h-4" />
                      </button>
                    </>
                  )}
                  {execution.status === 'paused' && (
                    <>
                      <button
                        onClick={() => onResume(execution.id)}
                        className="p-2 text-emerald-400 hover:bg-emerald-500/10 rounded-lg transition-colors"
                        title="恢复"
                      >
                        <Play className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onStop(execution.id)}
                        className="p-2 text-red-400 hover:bg-red-500/10 rounded-lg transition-colors"
                        title="停止"
                      >
                        <Square className="w-4 h-4" />
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => onViewDetail(execution.id)}
                    className="p-2 text-on-surface-variant hover:bg-surface-container rounded-lg transition-colors"
                    title="查看详情"
                  >
                    <MoreHorizontal className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
