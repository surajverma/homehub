/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
  // base.html sets data-theme="dark" on <html>, so dark: variants key off that attribute
  darkMode: ['selector', '[data-theme="dark"]'],
}
