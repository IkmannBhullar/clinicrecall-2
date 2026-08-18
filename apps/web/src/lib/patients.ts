/**
 * Patient-shaped API types, and the labels that go with them.
 *
 * Separate from `types.ts` so the shared vocabulary (statuses, errors, session) stays small and
 * the patient-specific surface can grow without crowding it.
 */

import type { PatientStatus, ReminderEventStatus, ReminderRuleKey } from "@/lib/types";

export type PatientSummary = {
  public_id: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string | null;

  last_annual_visit_date: string;
  next_annual_due_date: string;
  status: PatientStatus;
  scheduled_for: string | null;

  reminders_enabled: boolean;
  /** Whether the *patient* unsubscribed, as opposed to staff pausing them. */
  opted_out: boolean;

  last_reminder_at: string | null;
  last_reminder_status: string | null;
};

export type ReminderEvent = {
  id: string;
  status: ReminderEventStatus;
  channel: "EMAIL" | "SMS";
  source: "RULE" | "MANUAL" | "TEST";
  due_date_snapshot: string;
  scheduled_at: string;
  sent_at: string | null;
  delivered_at: string | null;
  failure_reason: string | null;
  rendered_subject: string | null;
};

export type PatientDetail = PatientSummary & {
  external_id: string | null;
  preferred_contact_method: "EMAIL" | "SMS" | "PHONE";
  opted_out_at: string | null;
  created_at: string;
  reminders: ReminderEvent[];
};

export type PatientListResponse = {
  patients: PatientSummary[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export type PatientActionResponse = {
  patient: PatientDetail;
  message: string;
};

export type RenderedMessage = {
  subject: string | null;
  html: string | null;
  text: string | null;
};

// ---------------------------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------------------------

export type DashboardResponse = {
  total_patients: number;
  due_this_month: number;
  overdue: number;
  reminders_sent_this_month: number;

  revenue: {
    appointments_recovered: number;
    estimated_value: string;
    value_per_visit: string;
    definition: string;
  };

  recall_overview: { status: PatientStatus; count: number }[];
  needs_attention: PatientSummary[];
  recent_reminders: {
    patient_public_id: string;
    patient_initials: string;
    status: string;
    sent_at: string | null;
    rule_key: string | null;
  }[];

  /** The practice's own current date, so the UI phrases "due in 11 days" from the same today. */
  today: string;
};

// ---------------------------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------------------------

/** How each delivery state is written for staff. */
export const REMINDER_STATUS_LABELS: Record<string, string> = {
  SCHEDULED: "Queued",
  SENDING: "Sending",
  SENT: "Sent",
  DELIVERED: "Delivered",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

/** Colour per delivery state. Failed is the only one that draws the eye, which is the point. */
export const REMINDER_STATUS_STYLES: Record<string, string> = {
  SCHEDULED: "bg-status-inactive-bg text-status-inactive",
  SENDING: "bg-status-scheduled-bg text-status-scheduled",
  SENT: "bg-status-scheduled-bg text-status-scheduled",
  DELIVERED: "bg-status-active-bg text-status-active",
  FAILED: "bg-status-overdue-bg text-status-overdue",
  CANCELLED: "bg-status-inactive-bg text-status-inactive",
};

export const RULE_SHORT_LABELS: Record<ReminderRuleKey | string, string> = {
  T_MINUS_30: "30 days before",
  T_MINUS_7: "7 days before",
  T_ZERO: "On due date",
  T_PLUS_30: "30 days after",
};
