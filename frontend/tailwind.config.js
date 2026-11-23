/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Electric-cyan primary (signature color event)
        primary: {
          DEFAULT: '#3478F6',
          deep: '#245FE5',
          soft: '#EAF2FF',
          bright: '#62B8FF', // dark-block highlight only
        },
        // Cool near-black ink scale
        ink: {
          DEFAULT: '#0F172A',
          secondary: '#334155',
          mute: '#64748B',
          faint: '#94A3B8',
        },
        // Surfaces
        canvas: {
          DEFAULT: '#FFFFFF',
          soft: '#F5F8FF',
          cool: '#EDF3FC',
        },
        hairline: {
          DEFAULT: '#E7EDF7',
          strong: '#D4DEEC',
        },
        // Dark terminal block (logs / code / commands)
        terminal: {
          DEFAULT: '#0B1120',
          line: '#1E293B',
          text: '#E2E8F0',
          mute: '#94A3B8',
        },
        // Status
        ok: '#10B981',
        warning: '#F59E0B',
        danger: '#EF4444',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'JetBrains Mono', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'monospace'],
      },
      // Plain `border` defaults to the hairline color so every card is consistent.
      borderColor: {
        DEFAULT: '#E2E8F0',
      },
      boxShadow: {
        card: '0 5px 18px rgba(56,102,174,0.07)',
        pop: '0 10px 28px rgba(56,102,174,0.12)',
      },
    },
  },
  plugins: [],
}
