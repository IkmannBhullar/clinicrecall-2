/**
 * Next.js configuration.
 *
 * The security headers below implement SPEC section 9. The Content-Security-Policy is the one
 * worth reading closely: it is what makes SPEC constraint D2 ("zero runtime network egress")
 * enforceable rather than aspirational. If someone later adds a CDN font or an analytics
 * snippet, the browser refuses to load it and the mistake shows up immediately in development
 * rather than as a stalled first paint on a clinic's captive-portal wifi.
 *
 * @type {import('next').NextConfig}
 */

// Read at build time so the policy can allow the API origin the app is actually configured for.
const apiOrigin = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
const supabaseOrigin = process.env.NEXT_PUBLIC_SUPABASE_URL || "http://127.0.0.1:54321";

const isDev = process.env.NODE_ENV === "development";

const contentSecurityPolicy = [
  // Nothing loads from anywhere by default. Every allowance below is deliberate.
  "default-src 'self'",

  // 'unsafe-eval' is required by React Fast Refresh in development only. It is absent from
  // production builds, which is where it would actually matter.
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,

  // Tailwind and React both inject styles inline at runtime.
  "style-src 'self' 'unsafe-inline'",

  // Self-hosted only. data: covers inline SVG icons. No remote image host is permitted.
  "img-src 'self' data: blob:",

  // Fonts are served from our own /fonts directory — never from a CDN (SPEC D2).
  "font-src 'self'",

  // The only two network destinations the app may talk to: our API and our Supabase instance.
  `connect-src 'self' ${apiOrigin} ${supabaseOrigin}${isDev ? " ws://localhost:* ws://127.0.0.1:*" : ""}`,

  // Clickjacking protection, and a hard stop on plugins and arbitrary form targets.
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: contentSecurityPolicy,
  },
  {
    // Stops a browser from second-guessing a response's declared content type, which is how
    // an uploaded file can end up being executed as script.
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    // Send the full URL only to ourselves. Patient identifiers must never leak via Referer.
    key: "Referrer-Policy",
    value: "same-origin",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    // Explicitly decline browser capabilities this app has no use for.
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
  {
    // HSTS is meaningful only over HTTPS; harmless on localhost, essential once deployed.
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig = {
  reactStrictMode: true,

  // This app lives in a monorepo, so the lockfile sits two directories up. Telling Next.js where
  // the workspace root is stops it guessing (and warning) on every build.
  outputFileTracingRoot: new URL("../../", import.meta.url).pathname,

  // Fail the production build on a type error or a lint error rather than shipping it.
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },

  // Do not advertise the framework version to anyone scanning the site.
  poweredByHeader: false,

  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
