"use client";

/**
 * The annual recall campaign (SPEC §8).
 *
 * Four rules, four toggles. **No automation builder** — SPEC §1 puts workflow builders out of
 * scope, and the offsets are not editable for the same reason: a configurable schedule is a
 * workflow builder wearing a different hat.
 *
 * That restraint is the feature. A clinic owner understands "we email them a month before, a week
 * before, on the day, and a month after" in one reading. A rules engine would need explaining,
 * and SPEC §1's governing principle is that a nontechnical employee should understand this in
 * about thirty seconds.
 *
 * Each toggle saves immediately. There is no Save button because there is nothing to batch — one
 * switch, one decision, instantly reversible.
 */

import * as React from "react";

import { Card, CardHeader, CardTitle, CardDescription, Spinner } from "@/components/ui/primitives";
import { toDisplayMessage, useApi } from "@/lib/use-api";
import { RULE_LABELS, type ReminderRuleKey } from "@/lib/types";
import type { ReminderRule } from "@/lib/settings";
import { cn } from "@/lib/utils";

/** What each rule is for, in the practice's terms rather than the schedule's. */
const RULE_PURPOSE: Record<ReminderRuleKey, string> = {
  T_MINUS_30: "An early heads-up, while there is still plenty of time to book.",
  T_MINUS_7: "A nudge in the week their visit becomes due.",
  T_ZERO: "On the day itself.",
  T_PLUS_30: "A final chase for patients who have not responded.",
};

export function CampaignRules({ initialRules }: { initialRules: ReminderRule[] }) {
  const api = useApi();
  const [rules, setRules] = React.useState(initialRules);
  const [saving, setSaving] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function toggle(rule: ReminderRule) {
    setSaving(rule.key);
    setError(null);

    // Optimistic: the switch moves at once. A toggle that waits for a round trip before
    // responding feels broken, and this change is trivially reversible if the request fails.
    const previous = rules;
    setRules((current) =>
      current.map((r) => (r.key === rule.key ? { ...r, enabled: !r.enabled } : r)),
    );

    try {
      await api.post<ReminderRule>(`/reminders/rules/${rule.key}`, { enabled: !rule.enabled });
    } catch (caught) {
      setRules(previous);
      setError(toDisplayMessage(caught));
    } finally {
      setSaving(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Annual recall campaign</CardTitle>
        <CardDescription>
          Four reminders around each patient&rsquo;s annual visit date. Turn any of them off and
          it stops sending immediately.
        </CardDescription>
      </CardHeader>

      {error ? (
        <p role="alert" className="mx-5 mb-3 rounded-control bg-danger-bg px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}

      <ul className="divide-y divide-border border-t border-border">
        {rules.map((rule) => (
          <li key={rule.key} className="flex items-center gap-4 px-5 py-3.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{RULE_LABELS[rule.key]}</p>
              <p className="mt-0.5 text-xs text-ink-subtle">{RULE_PURPOSE[rule.key]}</p>
            </div>

            {/*
              A real checkbox styled as a switch, rather than a div with a click handler.
              Keyboard activation, the accessibility tree, and form semantics all come free, and
              `role="switch"` is what a screen reader announces as "on"/"off" rather than
              "checked".
            */}
            <label className="relative inline-flex shrink-0 cursor-pointer items-center">
              <span className="sr-only">
                {rule.enabled ? "Turn off" : "Turn on"} the reminder {RULE_LABELS[rule.key]}
              </span>
              <input
                type="checkbox"
                role="switch"
                checked={rule.enabled}
                disabled={saving !== null}
                onChange={() => toggle(rule)}
                className="peer sr-only"
              />
              <span
                aria-hidden="true"
                className={cn(
                  "h-5 w-9 rounded-pill transition-colors",
                  "peer-focus-visible:ring-2 peer-focus-visible:ring-brand peer-focus-visible:ring-offset-2",
                  rule.enabled ? "bg-brand" : "bg-border-strong",
                )}
              />
              <span
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute left-0.5 size-4 rounded-pill bg-white shadow-card transition-transform",
                  rule.enabled && "translate-x-4",
                )}
              />
            </label>

            {saving === rule.key ? <Spinner className="text-ink-subtle" /> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
