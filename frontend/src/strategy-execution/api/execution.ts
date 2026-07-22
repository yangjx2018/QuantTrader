import request from '@/common/utils/request'
import type {
  Execution,
  ExecutionSignal,
  RiskRule,
  RiskAlert,
  ExecutionLog,
  ExecutionStatus,
  StartExecutionRequest,
  ApiResponse,
  PaginatedResponse,
} from '../types/execution'

export function getExecutionStatus() {
  return request.get<ApiResponse<ExecutionStatus>>('/execution/status')
}

export function startExecution(data: StartExecutionRequest) {
  return request.post<ApiResponse<Execution>>('/execution/start', data)
}

export function stopExecution(executionId: number) {
  return request.post<ApiResponse<Execution>>(`/execution/${executionId}/stop`)
}

export function pauseExecution(executionId: number) {
  return request.post<ApiResponse<Execution>>(`/execution/${executionId}/pause`)
}

export function resumeExecution(executionId: number) {
  return request.post<ApiResponse<Execution>>(`/execution/${executionId}/resume`)
}

export function getExecutionList(params?: { status?: string; strategy_id?: number; page?: number; page_size?: number }) {
  return request.get<ApiResponse<PaginatedResponse<Execution>>>('/execution/list', { params })
}

export function getExecutionDetail(executionId: number) {
  return request.get<ApiResponse<Execution>>(`/execution/${executionId}`)
}

export function forceExecutionTick(executionId: number) {
  return request.post(`/execution/${executionId}/tick`)
}

export function generateMockSignal(executionId: number) {
  // 兼容旧名：已改为真实 tick
  return forceExecutionTick(executionId)
}

export function getExecutionSignals(executionId: number, params?: { risk_passed?: boolean; start_date?: string; end_date?: string; page?: number; page_size?: number }) {
  return request.get<ApiResponse<PaginatedResponse<ExecutionSignal>>>(`/execution/${executionId}/signals`, { params })
}

export function getAllSignals(params?: { execution_id?: number; limit?: number }) {
  return request.get<ApiResponse<ExecutionSignal[]>>('/execution/signals', { params })
}

export function getRiskRules(enabled?: boolean) {
  return request.get<ApiResponse<RiskRule[]>>('/execution/risk-rules', {
    params: { enabled },
  })
}

export function createRiskRule(data: Omit<RiskRule, 'id' | 'created_at' | 'updated_at'>) {
  return request.post<ApiResponse<RiskRule>>('/execution/risk-rules', data)
}

export function getRiskRuleDetail(ruleId: number) {
  return request.get<ApiResponse<RiskRule>>(`/execution/risk-rules/${ruleId}`)
}

export function updateRiskRule(
  ruleId: number,
  data: Partial<Omit<RiskRule, 'id' | 'created_at' | 'updated_at'>>,
) {
  return request.put<ApiResponse<RiskRule>>(`/execution/risk-rules/${ruleId}`, data)
}

export function deleteRiskRule(ruleId: number) {
  return request.delete<ApiResponse<null>>(`/execution/risk-rules/${ruleId}`)
}

export function enableRiskRule(ruleId: number) {
  return request.post<ApiResponse<RiskRule>>(`/execution/risk-rules/${ruleId}/enable`)
}

export function disableRiskRule(ruleId: number) {
  return request.post<ApiResponse<RiskRule>>(`/execution/risk-rules/${ruleId}/disable`)
}

export function getActiveRiskAlerts() {
  return request.get<ApiResponse<RiskAlert[]>>('/execution/risk-alerts/active')
}

export function getRiskAlerts(params?: { execution_id?: number; level?: string; acknowledged?: boolean; page?: number; page_size?: number }) {
  return request.get<ApiResponse<PaginatedResponse<RiskAlert>>>('/execution/risk-alerts', { params })
}

export function acknowledgeRiskAlert(alertId: number) {
  return request.post<ApiResponse<RiskAlert>>(`/execution/risk-alerts/${alertId}/acknowledge`)
}

export function getExecutionLogs(executionId: number, params?: { level?: string; start_date?: string; end_date?: string; page?: number; page_size?: number }) {
  return request.get<ApiResponse<PaginatedResponse<ExecutionLog>>>(`/execution/${executionId}/logs`, { params })
}
