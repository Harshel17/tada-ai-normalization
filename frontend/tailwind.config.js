/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // a refined system stack: no webfont is loaded (no extra dependency, no network request)
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        // one restrained indigo accent
        brand: { 50: '#f5f7ff', 100: '#e9edff', 200: '#d4dcff', 300: '#b3bfff', 400: '#8b97f7', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca', 800: '#3730a3', 900: '#312e81' },
        ink: { DEFAULT: '#0f172a', soft: '#475569', faint: '#94a3b8' },
      },
      boxShadow: {
        card: '0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 1px rgba(15, 23, 42, 0.03)',
        pop: '0 8px 24px -8px rgba(15, 23, 42, 0.18)',
      },
      borderRadius: { xl: '0.875rem' },
    },
  },
  plugins: [],
}
