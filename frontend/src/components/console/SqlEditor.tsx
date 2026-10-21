import { useEffect, useRef } from 'react'

import { useUI } from '../../stores/ui'
import { LANGUAGE, THEME_DARK, THEME_LIGHT, monaco, setupMonaco } from './monacoSetup'

/**
 * The SQL console editor, on Monaco. Controlled like a textarea: `value` flows in, `onChange`
 * flows out, and Ctrl/Cmd+Enter calls `onRun`.
 */
export function SqlEditor({
  value,
  onChange,
  onRun,
  readOnly = false,
  minHeight = '190px',
}: {
  value: string
  onChange: (value: string) => void
  onRun?: () => void
  readOnly?: boolean
  minHeight?: string
}) {
  const theme = useUI((state) => state.theme)
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)

  // The editor is created once; the latest callbacks are read through refs so that re-rendering
  // the parent never tears it down.
  const onChangeRef = useRef(onChange)
  const onRunRef = useRef(onRun)
  onChangeRef.current = onChange
  onRunRef.current = onRun

  useEffect(() => {
    if (!host.current) return
    setupMonaco()
    const instance = monaco.editor.create(host.current, {
      value,
      language: LANGUAGE,
      theme: theme === 'dark' ? THEME_DARK : THEME_LIGHT,
      readOnly,
      ariaLabel: 'SQL query',
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      lineNumbersMinChars: 3,
      folding: false,
      renderLineHighlight: 'none',
      fontSize: 13,
      fontFamily: "'Geist Mono Variable', ui-monospace, SFMono-Regular, Menlo, monospace",
      wordWrap: 'on',
      padding: { top: 10, bottom: 10 },
      overviewRulerLanes: 0,
      scrollbar: { vertical: 'auto', horizontal: 'hidden' },
      // Words, not identifiers-only, so `COUNT(` and `GROUP BY` complete too.
      quickSuggestions: { other: true, comments: false, strings: false },
    })
    editor.current = instance

    const sub = instance.onDidChangeModelContent(() => onChangeRef.current(instance.getValue()))
    instance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => onRunRef.current?.())

    return () => {
      sub.dispose()
      instance.dispose()
      editor.current = null
    }
    // Created once. `value`, `theme` and `readOnly` are synchronised by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A template button replaces the text from outside; typing already matches and is skipped.
  useEffect(() => {
    const e = editor.current
    if (e && e.getValue() !== value) e.setValue(value)
  }, [value])

  useEffect(() => {
    monaco.editor.setTheme(theme === 'dark' ? THEME_DARK : THEME_LIGHT)
  }, [theme])

  useEffect(() => {
    editor.current?.updateOptions({ readOnly })
  }, [readOnly])

  return (
    <div
      ref={host}
      className="overflow-hidden text-small"
      style={{ height: minHeight, minHeight }}
    />
  )
}
