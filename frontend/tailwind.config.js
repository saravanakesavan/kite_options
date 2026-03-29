/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      keyframes: {
        'slide-in': {
          '0%':   { opacity: '0', transform: 'translateX(100%)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'shrink': {
          '0%':   { width: '100%' },
          '100%': { width: '0%' },
        },
      },
      animation: {
        'slide-in': 'slide-in 0.3s ease-out',
        'shrink':   'shrink linear forwards',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
  ],
}