/** Build a Tailwind colour from a CSS variable holding bare RGB channels. */
const rgb = (variable) => `rgb(var(${variable}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Channel-triplet vars so every utility supports the /opacity modifier.
      colors: {
        bg: rgb('--bg-rgb'),
        surface: rgb('--surface-rgb'),
        'surface-2': rgb('--surface-2-rgb'),
        border: rgb('--border-rgb'),
        'border-strong': rgb('--border-strong-rgb'),
        text: rgb('--text-rgb'),
        muted: rgb('--text-muted-rgb'),
        faint: rgb('--text-faint-rgb'),

        accent: {
          DEFAULT: rgb('--accent-rgb'),
          hover: rgb('--accent-hover-rgb'),
          press: rgb('--accent-press-rgb'),
          fg: rgb('--accent-fg-rgb'),
          weak: rgb('--accent-weak-rgb'),
        },

        ok: { DEFAULT: rgb('--ok-rgb'), weak: rgb('--ok-weak-rgb') },
        warn: { DEFAULT: rgb('--warn-rgb'), weak: rgb('--warn-weak-rgb') },
        danger: { DEFAULT: rgb('--danger-rgb'), weak: rgb('--danger-weak-rgb') },

        series: { 1: rgb('--accent-rgb'), 2: rgb('--series-2-rgb') },
      },
      borderColor: {
        DEFAULT: rgb('--border-rgb'),
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        DEFAULT: 'var(--radius)',
        md: 'var(--radius)',
        lg: 'var(--radius-lg)',
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        // Tracking tightens as the step grows: a grotesque set at display size
        // needs the counters pulled in or it reads loose next to mono figures.
        micro: ['12px', { lineHeight: '16px' }],
        small: ['13px', { lineHeight: '18px' }],
        body: ['14px', { lineHeight: '22px' }],
        h3: ['16px', { lineHeight: '22px', letterSpacing: '-0.014em' }],
        h2: ['20px', { lineHeight: '26px', letterSpacing: '-0.018em' }],
        display: ['30px', { lineHeight: '36px', letterSpacing: '-0.024em' }],
        hero: ['40px', { lineHeight: '46px', letterSpacing: '-0.03em' }],
      },
      boxShadow: {
        // One step of depth for a resting panel, one for anything that floats
        // above the page. Both tinted to the neutral ramp, never pure black.
        panel: 'var(--shadow-panel)',
        pop: 'var(--shadow-pop)',
        edge: 'var(--edge)',
        'panel-edge': 'var(--edge), var(--shadow-panel)',
        'pop-edge': 'var(--edge), var(--shadow-pop)',
      },
      maxWidth: {
        page: '1400px',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'fade-rise': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'none' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.97)' },
          to: { opacity: '1', transform: 'none' },
        },
        'slide-down': {
          from: { opacity: '0', transform: 'translateY(-4px)' },
          to: { opacity: '1', transform: 'none' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 120ms ease-out both',
        'fade-rise': 'fade-rise 180ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'scale-in': 'scale-in 140ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'slide-down': 'slide-down 120ms cubic-bezier(0.16, 1, 0.3, 1) both',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
