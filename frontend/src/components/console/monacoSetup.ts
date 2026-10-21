/**
 * One-time Monaco setup: bundled (no CDN), SQL only, skinned to the PrivGuard tokens.
 *
 * Imports are the ESM entry points rather than `monaco-editor` itself, which would pull every
 * language in. Only the editor core, its contributions (suggest widget, find, brackets) and the
 * PostgreSQL grammar are included. The console route is lazy, so this cost is only paid there.
 */
import 'monaco-editor/esm/vs/editor/editor.all'
import 'monaco-editor/esm/vs/basic-languages/pgsql/pgsql.contribution'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'

export { monaco }

export const LANGUAGE = 'pgsql'
export const THEME_DARK = 'privguard-dark'
export const THEME_LIGHT = 'privguard-light'

/** Columns the engine's binder accepts, wired into autocomplete (`SPEC.md` §3). */
const COLUMNS = ['total_charge', 'admission_type', 'dept_id', 'admit_ts', 'discharge_ts']
const KEYWORDS = ['SELECT', 'FROM', 'WHERE', 'AND', 'GROUP BY']
const FUNCTIONS = ['COUNT(*)', 'SUM(', 'AVG(']

const palette = {
  dark: {
    bg: '#131318',
    text: '#F4F4F6',
    muted: '#9B9BA6',
    faint: '#6E6E78',
    border: '#26262E',
    accent: '#5B8DEF',
    accentWeak: '#17233B',
    ok: '#3DBE8B',
    warn: '#E0A93A',
  },
  light: {
    bg: '#FFFFFF',
    text: '#18181B',
    muted: '#52525B',
    faint: '#A1A1AA',
    border: '#E4E4E7',
    accent: '#3B6FE0',
    accentWeak: '#EAF0FE',
    ok: '#1E9E6A',
    warn: '#B4791A',
  },
}

function define(name: string, mode: 'dark' | 'light') {
  const c = palette[mode]
  const hex = (v: string) => v.replace('#', '')
  monaco.editor.defineTheme(name, {
    base: mode === 'dark' ? 'vs-dark' : 'vs',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: hex(c.accent), fontStyle: 'bold' },
      { token: 'operator', foreground: hex(c.muted) },
      { token: 'predefined', foreground: hex(c.ok) },
      { token: 'string', foreground: hex(c.warn) },
      { token: 'number', foreground: hex(c.text) },
      { token: 'comment', foreground: hex(c.faint), fontStyle: 'italic' },
      { token: 'identifier', foreground: hex(c.text) },
    ],
    colors: {
      'editor.background': c.bg,
      'editor.foreground': c.text,
      'editorLineNumber.foreground': c.faint,
      'editorLineNumber.activeForeground': c.muted,
      'editorCursor.foreground': c.accent,
      'editor.selectionBackground': c.accentWeak,
      'editor.lineHighlightBackground': mode === 'dark' ? '#FFFFFF06' : '#00000005',
      'editorWidget.background': c.bg,
      'editorWidget.border': c.border,
      'editorSuggestWidget.background': c.bg,
      'editorSuggestWidget.border': c.border,
      'editorSuggestWidget.selectedBackground': c.accentWeak,
      'editorGutter.background': c.bg,
      focusBorder: '#00000000',
    },
  })
}

let ready = false

/** Safe to call repeatedly; the work happens once. */
export function setupMonaco() {
  if (ready) return
  ready = true

  // Monaco asks for a worker by label; the base editor worker is all the SQL grammar needs.
  ;(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
    getWorker: () => new EditorWorker(),
  }

  define(THEME_DARK, 'dark')
  define(THEME_LIGHT, 'light')

  monaco.languages.registerCompletionItemProvider(LANGUAGE, {
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      const item = (
        label: string,
        kind: monaco.languages.CompletionItemKind,
        detail: string,
      ): monaco.languages.CompletionItem => ({
        label,
        kind,
        detail,
        insertText: label,
        range,
      })
      const K = monaco.languages.CompletionItemKind
      return {
        suggestions: [
          ...KEYWORDS.map((k) => item(k, K.Keyword, 'keyword')),
          ...FUNCTIONS.map((f) => item(f, K.Function, 'private aggregate')),
          item('visits', K.Class, 'fact table'),
          ...COLUMNS.map((c) => item(c, K.Field, 'visits column')),
        ],
      }
    },
  })
}
