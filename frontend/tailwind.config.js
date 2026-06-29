/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'custom-green': '#53b146',
      },
      maxWidth: {
        review: '900px',
      },
    },
  },
  plugins: [],
};
