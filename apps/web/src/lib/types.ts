/**
 * Shapes the API returns.
 *
 * Hand-written rather than generated from the OpenAPI document. The surface is small enough that
 * a generator would be more machinery than it saves, and these read as documentation — the
 * comments explain what each field means to the product, which a generated file cannot.
 *
 * Keep in step with `apps/api/app/schemas/`. `tsc --noEmit` catches a field that is used and
 * missing; it cannot catch one the backend renamed, which is what the E2E suite is for.
 */

// ---------------------------------------------------------------------------------------------
// Errors (SPEC §9)
// ---------------------------------------------------------------------------------------------

export type ErrorResponse = {
  error: {
    /** Stable identifier. Branch on this, never on `message`, which gets reworded. */
    code: string;
    /** Safe to display. Never contains a stack trace or an internal identifier. */
    message: string;
    /** Locates this request in the server log. Worth showing when something goes wrong. */
    correlation_id: string;
    details?: { field: string; problem: string }[];
  };
};

// ---------------------------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------------------------

export type UserRole = "ADMIN" | "STAFF";

export type CurrentUser = {
  id: string;
  first_name: string;
  last_name: string;
  email: string;
  role: UserRole;
  /**
   * Informational only. The API always re-derives the tenant scope from the access token and
   * ignores any organization sent by a client (SPEC §3.2).
   */
  organization_id: string;
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
};

export type Session = {
  user: CurrentUser;
  organization: Organization;
};

// ---------------------------------------------------------------------------------------------
// Patients
// ---------------------------------------------------------------------------------------------

/** The seven recall states (SPEC §5.2). Derived, never set directly. */
export type PatientStatus =
  | "ACTIVE"
  | "DUE_SOON"
  | "DUE"
  | "OVERDUE"
  | "SCHEDULED"
  | "COMPLETED"
  | "INACTIVE";

export const PATIENT_STATUSES: readonly PatientStatus[] = [
  "OVERDUE",
  "DUE",
  "DUE_SOON",
  "SCHEDULED",
  "ACTIVE",
  "COMPLETED",
  "INACTIVE",
] as const;

/** How each status is written for a human. The enum values are for machines. */
export const STATUS_LABELS: Record<PatientStatus, string> = {
  ACTIVE: "Active",
  DUE_SOON: "Due soon",
  DUE: "Due",
  OVERDUE: "Overdue",
  SCHEDULED: "Scheduled",
  COMPLETED: "Completed",
  INACTIVE: "Inactive",
};

/** One line of explanation each, shown on hover. Staff should not have to guess. */
export const STATUS_DESCRIPTIONS: Record<PatientStatus, string> = {
  ACTIVE: "Not due for more than 30 days.",
  DUE_SOON: "Due within the next 30 days.",
  DUE: "Due now, or up to 7 days past due.",
  OVERDUE: "More than 7 days past due.",
  SCHEDULED: "Has an upcoming appointment booked.",
  COMPLETED: "Seen within the last 30 days.",
  INACTIVE: "Reminders paused, or the patient has opted out.",
};

// ---------------------------------------------------------------------------------------------
// Reminders
// ---------------------------------------------------------------------------------------------

export type ReminderRuleKey = "T_MINUS_30" | "T_MINUS_7" | "T_ZERO" | "T_PLUS_30";

export const RULE_LABELS: Record<ReminderRuleKey, string> = {
  T_MINUS_30: "30 days before due",
  T_MINUS_7: "7 days before due",
  T_ZERO: "On the due date",
  T_PLUS_30: "30 days after due",
};

export type ReminderEventStatus =
  | "SCHEDULED"
  | "SENDING"
  | "SENT"
  | "DELIVERED"
  | "FAILED"
  | "CANCELLED";
