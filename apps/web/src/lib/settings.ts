/** Settings, reminder-campaign, and activity API shapes. */

import type { ReminderRuleKey, UserRole } from "@/lib/types";

export type ClinicSettings = {
  clinic_name: string;
  phone: string | null;
  email: string | null;
  website: string | null;
  scheduling_url: string | null;
  timezone: string;
  annual_interval_months: number;
  estimated_annual_visit_value: string;
  reminder_signature: string | null;
};

export type ReminderRule = {
  key: ReminderRuleKey;
  days_relative_to_due_date: number;
  enabled: boolean;
};

export type SettingsPage = {
  clinic: ClinicSettings;
  rules: ReminderRule[];
  account: { first_name: string; last_name: string; email: string; role: UserRole };
  demo_utilities_enabled: boolean;
};

export type ReminderPerformance = {
  scheduled: number;
  sent: number;
  delivered: number;
  failed: number;
  total: number;
};

export type FailedReminder = {
  id: string;
  patient_public_id: string;
  patient_name: string;
  patient_email: string;
  failure_reason: string | null;
  failed_at: string | null;
};

export type ActivityEntry = {
  id: string;
  type: string;
  created_at: string;
  actor_initials: string | null;
  patient_initials: string | null;
  patient_public_id: string | null;
  summary: string;
  payload: Record<string, unknown>;
};

export type ActivityResponse = {
  entries: ActivityEntry[];
  has_more: boolean;
};

// ---------------------------------------------------------------------------------------------
// CSV import (SPEC §7)
// ---------------------------------------------------------------------------------------------

export type RowProblem = {
  row_number: number;
  category: string;
  column: string;
  value: string;
  reason: string;
};

export type ImportPreview = {
  total_rows: number;
  valid_rows: number;
  new_count: number;
  update_count: number;
  missing_required: number;
  invalid_email: number;
  invalid_date: number;
  duplicate_in_file: number;
  problems: RowProblem[];
};

export type ImportResult = {
  created: number;
  updated: number;
  skipped: number;
  total_rows: number;
};

/** How each rejection category is written for the practice. */
export const PROBLEM_LABELS: Record<string, string> = {
  missing_required: "Missing required information",
  invalid_email: "Invalid email address",
  invalid_date: "Unusable last-visit date",
  duplicate: "Duplicate row",
};
