/**
 * Root layout — wraps every page in the application.
 *
 * Kept intentionally thin. The signed-in application chrome (sidebar navigation, header, the
 * persistent demo-data indicator) arrives in phase 8 as a nested layout, so that the sign-in
 * page and the unsubscribe confirmation page can render without it.
 */

import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "ClinicRecall",
    // Page titles read as "Patients · ClinicRecall", which is what a user scanning browser
    // tabs actually needs to see.
    template: "%s · ClinicRecall",
  },
  description: "Identify patients due for their annual visit and send professional reminders.",

  // This app displays synthetic demo records. Even so, there is no reason for any page of a
  // patient-recall tool to be indexed by a search engine.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Never block pinch-zoom. Capping user scaling is an accessibility failure, and this app is
  // used by people reading dense tables.
  maximumScale: 5,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        {/* Keyboard users land here first and can jump straight past the navigation. */}
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
