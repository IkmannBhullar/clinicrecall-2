/**
 * The navigation, as data.
 *
 * **Five items. Nothing else.** SPEC §8 names them exactly: Dashboard, Patients, Reminders,
 * Activity, Settings. That list is the scope of the product made visible — SPEC §1 warns that
 * scope creep is the primary failure mode here, and a sixth nav item is what that failure looks
 * like on the day it starts.
 *
 * Kept in one file so the sidebar, the mobile menu, and any breadcrumb all read from the same
 * source and cannot disagree.
 */

import { Activity, LayoutDashboard, Send, Settings, Users, type LucideIcon } from "lucide-react";

export type NavigationItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Read by screen readers on the link, where the label alone lacks context. */
  description: string;
};

export const NAVIGATION: readonly NavigationItem[] = [
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
    description: "Recall overview and the patients needing attention",
  },
  {
    href: "/patients",
    label: "Patients",
    icon: Users,
    description: "Search, filter, and act on individual patients",
  },
  {
    href: "/reminders",
    label: "Reminders",
    icon: Send,
    description: "The annual recall campaign and its delivery performance",
  },
  {
    href: "/activity",
    label: "Activity",
    icon: Activity,
    description: "Everything that has happened, most recent first",
  },
  {
    href: "/settings",
    label: "Settings",
    icon: Settings,
    description: "Clinic profile, reminder settings, and your account",
  },
] as const;

/**
 * Which nav item a path belongs to.
 *
 * Prefix matching, so `/patients/7K2QW9XR4TVB` keeps "Patients" highlighted. Without it, opening
 * a patient makes the sidebar look as though you have navigated away from the section you are
 * plainly still in.
 */
export function isActivePath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
