/**
 * ESLint configuration (flat config).
 *
 * Beyond the Next.js defaults, this adds one project-specific rule that is a security control
 * rather than a style preference: nothing under apps/web may reference the Supabase
 * service-role key. See the `no-restricted-properties` and `no-restricted-syntax` entries below.
 */

import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// eslint-config-next is still published in the older "extends" format, so it is bridged in.
const compat = new FlatCompat({ baseDirectory: __dirname });

const config = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),

  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },

  {
    rules: {
      /*
       * SECURITY RULE (SPEC section 3.2).
       *
       * The Supabase service-role key bypasses every access control in the database. If it is
       * ever read in this package it will be inlined into JavaScript served to browsers, and
       * every patient record becomes readable by anyone who opens dev tools.
       *
       * scripts/check-bundle-secrets.sh catches this at build time; this rule catches it while
       * you are still typing, which is considerably cheaper.
       */
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='env'][property.name=/SERVICE_ROLE|JOB_TOKEN|UNSUBSCRIBE_TOKEN_SECRET|PROVIDER_API_KEY/]",
          message:
            "Server-side secrets must never be read in apps/web — they would be compiled into the browser bundle. Call the API instead.",
        },
      ],

      // Unused variables are usually a leftover from a refactor. Allow a leading underscore for
      // the genuinely-intentional cases (unused route params, destructured rest).
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
