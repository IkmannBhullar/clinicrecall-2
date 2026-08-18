/**
 * Typeface loading.
 *
 * Inter, served from our own origin — never from a CDN, and never through `next/font/google`.
 *
 * SPEC constraint D2 is the reason: this product is demonstrated in clinics, on guest wifi that
 * is routinely captive-portalled or firewalled. A font request to `fonts.gstatic.com` that hangs
 * takes the whole first paint with it, and the demo opens on a blank screen in front of the
 * customer.
 *
 * `next/font/google` would in fact self-host the result — it downloads at build time and serves
 * the file locally — but it needs network access *during the build*, which means a clean clone
 * cannot be built offline. The font file is committed instead: 344 KB, once, and the repository
 * builds anywhere.
 *
 * The variable font is one file covering every weight from 100 to 900. Shipping the static cuts
 * instead would mean four or five separate downloads to get regular, medium, semibold and bold.
 */

import localFont from "next/font/local";

export const inter = localFont({
  src: "../fonts/InterVariable.woff2",
  // Exposed as a CSS variable so Tailwind's `--font-sans` token can point at it (see
  // globals.css) rather than the class being applied by hand on every element.
  variable: "--font-inter",
  // `swap` shows the fallback immediately and switches when the font arrives. With a
  // same-origin file this is near-instant, but it guarantees text is never invisible — which is
  // the failure mode that makes a slow page look like a broken one.
  display: "swap",
  weight: "100 900",
  // Matched to Inter's metrics so the swap does not shift the layout.
  fallback: [
    "ui-sans-serif",
    "system-ui",
    "-apple-system",
    "Segoe UI",
    "Roboto",
    "Helvetica Neue",
    "Arial",
    "sans-serif",
  ],
});
