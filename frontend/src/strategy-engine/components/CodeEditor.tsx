/**
 * Monaco 代码编辑器包装组件。
 *
 * 特性：
 * - 深色主题（vs-dark），与项目 TradingView 风格一致
 * - Python 语法高亮
 * - 受控组件：value + onChange
 * - 错误标注：传入 errors 时在对应行显示红色波浪线（marker）
 * - 字符数 + 行数显示
 *
 * 依赖：@monaco-editor/react（运行时从 CDN 加载 monaco-editor，避免打包膨胀）
 */

import { useEffect, useRef } from 'react'
import Editor, { type OnMount } from '@monaco-editor/react'
import type { editor, MarkerData } from 'monaco-editor'

import type { ValidationIssue } from '../types/strategy'

interface CodeEditorProps {
  value: string
  onChange?: (value: string) => void
  /** 校验问题：error 严重级别标记为红色波浪线 */
  errors?: ValidationIssue[]
  /** 只读模式（如查看内置策略） */
  readOnly?: boolean
  /** 最小高度（默认 480px） */
  minHeight?: number
}

export function CodeEditor({
  value,
  onChange,
  errors = [],
  readOnly = false,
  minHeight = 480,
}: CodeEditorProps) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null)

  const handleMount: OnMount = (ed, monaco) => {
    editorRef.current = ed

    // 定义项目自定义暗色主题（与 globals.css @theme 对齐）
    monaco.editor.defineTheme('quantflow-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: 'comment', foreground: '6b7280', fontStyle: 'italic' },
        { token: 'string', foreground: 'a3e635' },
        { token: 'number', foreground: 'fbbf24' },
        { token: 'keyword', foreground: '60a5fa' },
        { token: 'delimiter', foreground: '9ca3af' },
      ],
      colors: {
        'editor.background': '#12121a',
        'editor.foreground': '#e5e7eb',
        'editorLineNumber.foreground': '#4b5563',
        'editorLineNumber.activeForeground': '#9ca3af',
        'editor.selectionBackground': '#1d4ed833',
        'editor.lineHighlightBackground': '#1a1a2e',
        'editorCursor.foreground': '#3b82f6',
        'editorWidget.background': '#16162a',
        'editorWidget.border': '#2a2a3e',
        'editorGutter.background': '#0e0e16',
        'scrollbarSlider.background': '#2a2a3e88',
      },
    })
    monaco.editor.setTheme('quantflow-dark')

    // 应用初始 markers
    updateMarkers(ed, monaco, errors)
  }

  // errors 变化时更新 markers
  useEffect(() => {
    if (!editorRef.current) return
    // @monaco-editor/react 已经把 monaco 注入到 window.monaco
    const monaco = (window as unknown as { monaco?: typeof import('monaco-editor') }).monaco
    if (!monaco) return
    updateMarkers(editorRef.current, monaco, errors)
  }, [errors])

  return (
    <div className="border border-outline-variant/20 rounded-md overflow-hidden bg-surface-container-lowest">
      <Editor
        height={minHeight}
        defaultLanguage="python"
        value={value}
        onChange={(v) => onChange?.(v ?? '')}
        onMount={handleMount}
        theme="quantflow-dark"
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: '"JetBrains Mono", ui-monospace, monospace',
          fontLigatures: true,
          lineNumbers: 'on',
          renderLineHighlight: 'all',
          scrollBeyondLastLine: false,
          smoothScrolling: true,
          cursorBlinking: 'smooth',
          cursorSmoothCaretAnimation: 'on',
          tabSize: 4,
          insertSpaces: true,
          automaticLayout: true,
          wordWrap: 'on',
          padding: { top: 12, bottom: 12 },
          scrollbar: {
            verticalScrollbarSize: 10,
            horizontalScrollbarSize: 10,
          },
          readOnly,
          domReadOnly: readOnly,
        }}
      />
    </div>
  )
}

/**
 * 把 ValidationIssue 列表转换为 Monaco marker 并更新到 editor model。
 */
function updateMarkers(
  ed: editor.IStandaloneCodeEditor,
  monaco: typeof import('monaco-editor'),
  issues: ValidationIssue[],
): void {
  const model = ed.getModel()
  if (!model) return

  const markers: MarkerData[] = issues
    .filter((i) => i.line != null && i.line > 0)
    .map((i) => ({
      startLineNumber: i.line!,
      startColumn: i.column ?? 1,
      endLineNumber: i.line!,
      endColumn: (i.column ?? 1) + 100, // 标到行尾
      message: i.message,
      severity:
        i.severity === 'error'
          ? monaco.MarkerSeverity.Error
          : monaco.MarkerSeverity.Warning,
    }))

  monaco.editor.setModelMarkers(model, 'strategy-engine', markers)
}
