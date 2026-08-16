/**
 * PostCSS configuration.
 *
 * Tailwind v4 is a single PostCSS plugin and takes its configuration from CSS rather than from a
 * JavaScript config file — see the `@theme` block in src/app/globals.css.
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
