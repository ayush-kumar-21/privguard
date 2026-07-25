import { sql, PostgreSQL } from '@codemirror/lang-sql'
import CodeMirror, { EditorView, keymap, Prec } from '@uiw/react-codemirror'
import { useMemo } from 'react'

import { useUI } from '../../stores/ui'
import { sqlThemeDark, sqlThemeLight } from './editorTheme'

/** Columns the gateway's parser accepts, wired into autocomplete. */
const SCHEMA = {
  visits: [
    'patient_id',
    'total_charge',
    'admission_type',
    'dept_id',
    'admit_ts',
    'discharge_ts',
  ],
}

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

  const extensions = useMemo(
    () => [
      sql({ dialect: PostgreSQL, schema: SCHEMA, upperCaseKeywords: true }),
      EditorView.lineWrapping,
      // Prec.highest so Cmd/Ctrl+Enter beats the default newline binding.
      Prec.highest(
        keymap.of([
          {
            key: 'Mod-Enter',
            preventDefault: true,
            run: () => {
              onRun?.()
              return true
            },
          },
        ]),
      ),
    ],
    [onRun],
  )

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      theme={theme === 'dark' ? sqlThemeDark : sqlThemeLight}
      extensions={extensions}
      editable={!readOnly}
      basicSetup={{
        lineNumbers: true,
        foldGutter: false,
        highlightActiveLine: false,
        highlightActiveLineGutter: false,
        autocompletion: true,
        bracketMatching: true,
        closeBrackets: true,
        searchKeymap: false,
      }}
      style={{ fontSize: 13, minHeight }}
      className="cm-privguard overflow-hidden text-small"
      aria-label="SQL query"
    />
  )
}
