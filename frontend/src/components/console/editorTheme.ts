import { createTheme } from '@uiw/codemirror-themes'
import { tags as t } from '@lezer/highlight'

/**
 * CodeMirror skinned to the PrivGuard tokens rather than a stock theme.
 * Built twice because CodeMirror resolves its colours once at extension
 * creation time, so CSS variables cannot carry the light/dark swap here.
 */
function build(mode: 'light' | 'dark') {
  const dark = mode === 'dark'
  const c = dark
    ? {
        bg: '#131318',
        surface2: '#1B1B22',
        text: '#F4F4F6',
        muted: '#9B9BA6',
        faint: '#6E6E78',
        border: '#26262E',
        accent: '#5B8DEF',
        accentWeak: '#17233B',
        ok: '#3DBE8B',
        warn: '#E0A93A',
        danger: '#EF5A5A',
      }
    : {
        bg: '#FFFFFF',
        surface2: '#F4F4F5',
        text: '#18181B',
        muted: '#52525B',
        faint: '#A1A1AA',
        border: '#E4E4E7',
        accent: '#3B6FE0',
        accentWeak: '#EAF0FE',
        ok: '#1E9E6A',
        warn: '#B4791A',
        danger: '#D64545',
      }

  return createTheme({
    theme: mode,
    settings: {
      background: c.bg,
      foreground: c.text,
      caret: c.accent,
      selection: c.accentWeak,
      selectionMatch: c.accentWeak,
      lineHighlight: dark ? 'rgba(255,255,255,0.025)' : 'rgba(0,0,0,0.02)',
      gutterBackground: c.bg,
      gutterForeground: c.faint,
      gutterBorder: 'transparent',
      fontFamily: 'var(--font-mono)',
    },
    styles: [
      { tag: [t.keyword, t.operatorKeyword], color: c.accent, fontWeight: '500' },
      { tag: [t.function(t.variableName), t.standard(t.variableName)], color: c.ok },
      { tag: [t.string, t.special(t.string)], color: c.warn },
      { tag: [t.number, t.bool, t.null], color: c.text },
      { tag: [t.comment, t.lineComment, t.blockComment], color: c.faint, fontStyle: 'italic' },
      { tag: [t.variableName, t.propertyName], color: c.text },
      { tag: [t.operator, t.punctuation, t.separator], color: c.muted },
      { tag: t.invalid, color: c.danger },
    ],
  })
}

export const sqlThemeDark = build('dark')
export const sqlThemeLight = build('light')
