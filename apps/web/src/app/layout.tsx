/**
 * Root layout — wraps every page.
 *
 * Deliberately thin. The signed-in chrome lives in `(app)/layout.tsx`, so the sign-in page and
 * the unsubscribe confirmation render without a sidebar for a session that does not exist.
 */

import type { Metadata, Viewport } from "next";

import { inter } from "@/lib/fonts";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "ClinicRecall",
    // Reads as "Patients · ClinicRecall" — which is what someone scanning a row of browser tabs
    // actually needs.
    template: "%s · ClinicRecall",
  },
  description: "Identify patients due for their annual visit and send professional reminders.",

  // This instance holds synthetic records, but there is no reason for any page of a
  // patient-recall tool to be indexed.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Pinch-zoom is never blocked. Capping user scaling is an accessibility failure, and this
  // application is read by people working through dense tables.
  maximumScale: 5,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        {/* Keyboard users land here first and can jump straight past the navigation. Visible
            only while focused, which is why it is not simply hidden. */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-control focus:bg-surface focus:px-4 focus:py-2 focus:text-ink focus:shadow-panel"
        >
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
