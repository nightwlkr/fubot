/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#0B0E11',
        surface: '#151920',
        surface2: '#1C2028',
        border: '#2A2F3A',
        primary: '#3B82F6',
        success: '#22C55E',
        danger: '#EF4444',
        warning: '#F59E0B',
        text: '#F9FAFB',
        muted: '#6B7280',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
