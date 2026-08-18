# Demo script

The literal talk track for showing ClinicRecall to a clinic owner or office manager. It takes
about eight minutes at a comfortable pace.

The thirteen steps below are the same thirteen executed by
[`demo-walkthrough.spec.ts`](../apps/web/e2e/demo-walkthrough.spec.ts) on every `make verify`, and
each one writes the screenshot in [`screenshots/`](./screenshots/). The script and the product
therefore cannot drift apart without a test going red — if a step here does not match what you see
on screen, that is a bug in the product, not a stale document.

## Before you walk in

- [ ] `make demo-reset` — under 30 seconds, puts every fixture back in its documented state. Do
      this immediately before the meeting, not the night before: the seed is dated relative to
      today, so "24 days overdue" only reads correctly if it was computed recently.
- [ ] `make dev`, then confirm the dashboard loads and Sarah Johnson shows as **Overdue**.
- [ ] Confirm the laptop does **not** need wifi. Nothing in the running app makes a network
      request — fonts are self-hosted, there is no CDN, no analytics, no remote asset. Turn the
      wifi off and demo anyway; it is a better demo for it.
- [ ] Have `docs/samples/patients-messy.csv` somewhere you can find it in a file picker under
      pressure.

Sign-in is `alex.morgan@greenvalley.example.com` / `ClinicRecallDemo2026!`, pre-filled on the
sign-in page so you are never typing a password in front of an audience.

## The three questions you will be asked

Every clinic asks these. The honest answers matter more than the polished ones, and all three are
answerable without leaving the product.

### 1. "Is this HIPAA compliant?"

**No — and nothing in this product claims to be.**

Say it plainly and without flinching; hedging here is what loses the room. Then say what is
actually true: this is a demonstration running on synthetic patient records, which is why the
"Demo Data — synthetic patients only" marker sits in the header of every screen and cannot be
turned off.

Then say what compliance would actually require, because a vendor who can list it is a vendor who
has thought about it. [`SECURITY.md`](./SECURITY.md) has the full list; the short version is
production infrastructure with encryption and key management, an independent security review,
Business Associate Agreements with every vendor that touches PHI, audit logging and retention
policies, and workforce training. That is a programme of work, not a checkbox, and any vendor who
answers this question with an unqualified "yes" in a first meeting is telling you something about
how they will answer the next one.

What you *can* show is that the engineering underneath is not careless: tenancy is enforced at the
repository layer so one practice cannot read another's rows, application logs are redacted so
patient details never reach them, and the audit trail stores IDs and initials rather than names.

### 2. "How do patients opt out?"

**One click, no login, from the email itself.**

Every reminder carries an unsubscribe link with a signed token. Opening it stops reminders for
that patient immediately — no account, no password, no "reply STOP and wait". Show it: open a
patient, click **View the email that was sent**, and the link is right there in the footer.

Three details worth volunteering, because they are the ones that get asked next:

- It is idempotent. Someone unsure whether it worked will click twice, and the second click says
  the same thing as the first rather than an error.
- Staff cannot silently re-enable it. A patient who opted out themselves shows as opted out in the
  drawer, and the resume control refuses with an explanation rather than quietly turning reminders
  back on.
- It is recorded in the activity feed, so there is a record of when consent was withdrawn.

### 3. "Where does the revenue number come from?"

**From a deliberately conservative count, and the formula is on the screen.**

Do not paraphrase this one — click **How is this calculated?** on the dashboard card and read what
it says. The number is patients who received at least one *delivered* reminder and were then marked
as scheduled within 30 days of it, multiplied by the practice's own estimated value per visit.

Then volunteer the three things that make it conservative, because whoever is paying attention is
already thinking of them:

- It counts *delivered*, not merely sent. A reminder the mail server accepted but never landed does
  not count.
- The appointment must be booked *after* the reminder, not merely near it.
- Each patient counts once, however many reminders they received.

And say the honest part out loud: it is labelled an estimate because some of those patients would
have booked anyway. The product says so in the definition text. A recall tool that claims credit
for every appointment in the window is not measuring anything.

## The walkthrough

### 1. Sign in

Start on the sign-in screen rather than an already-open dashboard — it is worth three seconds to
show that this is a real application with real accounts. Point at the **Demo Data — synthetic
patients only** chip in the corner: it is there before anyone is even signed in.

> "Everything you're about to see is fake patients. That marker never comes off."

### 2. The dashboard

> "This is what your office manager opens on Monday morning. Fifty-five patients, nineteen due
> this month, eight already overdue."

Let the numbers sit for a moment. The point of this screen is that it answers "what needs doing
today" without anyone running a report.

### 3. Filter to overdue

Click **Patients**, then the **Overdue** chip. Eight patients.

> "This is the list that today doesn't exist at most practices. Not because nobody cares — because
> pulling it out of the practice management system takes an afternoon."

### 4. Open Sarah Johnson

Click her name. The drawer opens: 24 days overdue.

> "Sarah was due last month. Nobody has called her, and nobody was going to."

### 5. The timeline

Two reminders already went out, both delivered. Click **View the email that was sent**.

> "This is exactly what she received — not a template, the actual message. If a patient rings up
> confused about an email, you can see what they're looking at."

This is also where the unsubscribe link is visible, which sets up question 2 before it is asked.

### 6. Send a reminder

Click **Send reminder**.

> "That's the third one. In a real deployment this goes out on a schedule — you're watching me do
> manually what the system does at 6am."

### 7. Delivery lands

The timeline grows by one and shows the send was accepted.

> "Sent, and the provider took it. If it had bounced you'd see that too — I'll show you the bounce
> handling in a minute."

### 8. Mark scheduled

Sarah calls back and books. Click **Mark scheduled**. The badge moves from **Overdue** to
**Scheduled**.

> "That's the whole point of the product, in one click. She's off the overdue list, and the system
> knows when she's next due."

Close the drawer.

### 9. The dashboard moved

Go back to **Dashboard**. Overdue is now 7, scheduled is 5.

> "One click over there, and this number moved. It's one system, not four screens that each keep
> their own version of the truth."

This is the step that convinces engineers in the room, and it is worth the extra beat.

### 10. Import a real export

Go to **Import** and choose `patients-messy.csv`.

> "This is the part everyone worries about. Your patient list is a mess — mine is too, deliberately.
> Different date formats, some blank fields, a few bad email addresses."

### 11. The preview

327 records found, 320 ready to import, 5 missing information, 2 invalid emails.

> "Nothing has been written yet. It's telling you what will happen before it happens — and every
> row it's going to skip, with the line number and the reason."

Click **Download error report** if there is interest.

> "That downloads as a CSV you can open next to your export and fix. It's not a wall of red text
> telling you your file is bad."

### 12. Reminders

> "Four rules — 30 days before, 7 days before, on the day, 30 days after. Toggles, not a workflow
> builder. Nobody at a clinic wants to build an automation."

Scroll to **Reminders that failed**. Robert Hale's address hard-bounced.

> "This is the bit most tools skip. A reminder that failed is a patient who thinks nobody called.
> It's here, it's visible, and you can act on it."

### 13. Close on the revenue number

Back to **Dashboard**, and click **How is this calculated?**

Then answer question 3 above, out loud, whether or not it has been asked. Ending the demo by
volunteering the limitations of your own headline number is the single most persuasive thing in the
script.

> "Four appointments recovered. I've told you exactly how I counted them, and I've told you why
> it's called an estimate. You can decide what that's worth to you."

## If something goes wrong

- **A number reads oddly** (someone "0 days overdue", a date that looks off) — the seed is dated
  relative to today and the demo has probably been running since before midnight. Run
  `make demo-reset` and reload.
- **You have already clicked through once and want to reset mid-meeting** — Settings has the reset
  control, fenced off in the admin section. It takes the same 30 seconds.
- **Anything requires the internet** — that is a bug. Note it and carry on; the demo does not need
  a connection and neither should the product.
