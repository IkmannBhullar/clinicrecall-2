# Demo script

> **Status:** the literal talk track for demonstrating ClinicRecall to a clinic owner. Written in
> phase 12, once every screen it refers to actually exists — a demo script that describes
> imaginary buttons is worse than no script.

## Before you walk in

- [ ] `make demo-reset` — takes under 30 seconds and puts every fixture back in its documented
      state. Do this immediately before the meeting, not the night before.
- [ ] `make dev` — confirm the dashboard loads and Sarah Johnson shows as OVERDUE.
- [ ] Confirm the laptop does **not** need wifi. Nothing in the app makes a network request; if
      you find something that does, that is a bug, not a workaround.

## The three questions you will be asked

Every clinic asks these. The honest answers matter more than the polished ones.

**1. "Is this HIPAA compliant?"**

_Answer arrives in phase 12 — but the substance is already fixed by
[`SECURITY.md`](./SECURITY.md): no, and here is precisely what compliance would require._

**2. "How do patients opt out?"**

_Answer arrives in phase 12, once the tokenized unsubscribe endpoint is built in phase 5._

**3. "Where does the revenue number come from?"**

_Answer arrives in phase 12, once the dashboard exposes the formula in phase 9._

## The walkthrough

_The thirteen steps, with what to click and what to say at each, arrive in phase 12. The same
thirteen steps are executed by the Playwright suite in phase 11, so the script and the product
cannot drift apart without a test failing._
