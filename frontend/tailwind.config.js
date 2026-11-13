/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Business360-inspired brand palette: blue first, cyan support, no purple.
        primary: {
          DEFAULT: '#2878D0',
          deep: '#1F65B3',
          soft: '#EAF3FC',
          bright: '#58A6F2',
        },
        brand: {
          blue: '#2878D0',
          cyan: '#13A6B5',
          green: '#22A477',
          orange: '#E99A32',
        },
        cyan: {
          DEFAULT: '#13A6B5',
          deep: '#0E8794',
          soft: '#E5F6F4',
        },
        ink: {
          DEFAULT: '#1B2B3D',
          secondary: '#40556B',
          mute: '#687D91',
          faint: '#91A1B1',
        },
        canvas: {
          DEFAULT: '#FFFFFF',
          soft: '#F3F6FA',
          cool: '#EAF0F6',
          blue: '#EEF5FC',
        },
        surface: {
          blue: '#EAF3FC',
          cyan: '#E5F6F4',
          green: '#E8F6EF',
          orange: '#FFF3E3',
        },
        hairline: {
          DEFAULT: '#E1E9F1',
          strong: '#C9D6E3',
        },
        terminal: {
          DEFAULT: '#101B2A',
          line: '#26374A',
          text: '#E5EDF5',
          mute: '#9AABBD',
        },
        ok: '#22A477',
        warning: '#E99A32',
        danger: '#D95C5C',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'JetBrains Mono', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'monospace'],
      },
      borderColor: {
        DEFAULT: '#E1E9F1',
      },
      boxShadow: {
        card: '0 2px 8px rgba(35, 70, 105, 0.055), 0 1px 2px rgba(35, 70, 105, 0.035)',
        pop: '0 8px 24px rgba(35, 70, 105, 0.11), 0 2px 6px rgba(35, 70, 105, 0.045)',
        lift: '0 12px 28px rgba(40, 120, 208, 0.12), 0 3px 8px rgba(35, 70, 105, 0.05)',
        inset-soft: 'inset 0 1px 0 rgba(255,255,255,0.8)',
      },
      borderRadius: {
        card: '14px',
        panel: '18px',
      },
    },
  },
  plugins: [],
}
