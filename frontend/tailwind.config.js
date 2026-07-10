/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  // On touch devices a tap leaves the element in :hover until you tap elsewhere, so
  // `hover:bg-*` styles "stick" (e.g. Mark done staying filled green after a tap).
  // This wraps every `hover:` variant in `@media (hover:hover)` so hover styles only
  // apply where a real pointer can hover — desktop is unchanged; touch no longer sticks.
  future: { hoverOnlyWhenSupported: true },
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
