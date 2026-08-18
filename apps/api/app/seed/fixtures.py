"""The demo data, described declaratively (SPEC §7.3).

Everything the seed creates is defined here as data, and built by ``runner.py``. Keeping the two
apart means the answer to "what will the demo show?" is a file you can read rather than a
procedure you have to trace.

Two properties matter more than anything else in this file.

**Determinism (SPEC constraint D4).** The demo script names six patients and claims exact states
for them. Those claims are spoken aloud in front of a clinic owner, so they cannot be
approximately true. Each named fixture below states its intended status, and a test asserts the
seeded database matches.

**Relative dates (SPEC constraint D1).** Every date is ``today ± N days``, computed at seed time.
A demo built on hardcoded dates is wrong within a week, and every status badge on screen quietly
starts lying.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.models.enums import PatientStatus, ReminderEventStatus, ReminderRuleKey, UserRole

# ---------------------------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------------------------

ORGANIZATION_NAME = "Green Valley Family Clinic"
ORGANIZATION_SLUG = "green-valley-family-clinic"

CLINIC_TIMEZONE = "America/Los_Angeles"
CLINIC_PHONE = "555-0142"
CLINIC_EMAIL = "office@greenvalley.example.com"
CLINIC_WEBSITE = "https://greenvalley.example.com"
CLINIC_SCHEDULING_URL = "https://greenvalley.example.com/book"
CLINIC_SIGNATURE = "Warm regards,\nThe Green Valley team"

#: What one recovered annual visit is worth. Feeds "Estimated Revenue Recovered".
ESTIMATED_VISIT_VALUE = "250.00"

#: Namespace for deterministic identifiers.
#:
#: Every seeded row gets a UUID derived from its stable key rather than a random one. Two things
#: follow: re-running the seed collides on the primary key instead of inserting a duplicate
#: (which is how idempotency is achieved), and a patient keeps the same URL across a
#: `make demo-reset` — so a bookmark, a screenshot, or a Playwright test written last week still
#: points at Sarah Johnson.
SEED_NAMESPACE = uuid.UUID("6f3c1b52-8d4a-4a1e-9f27-1c9a5e0b7d31")


def seed_uuid(*parts: str) -> uuid.UUID:
    """A stable UUID for a seeded row, derived from its natural key."""
    return uuid.uuid5(SEED_NAMESPACE, "|".join(parts))


# Crockford base32, matching app.core.ids — so a seeded public_id is indistinguishable in shape
# from a generated one.
_PUBLIC_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def seed_public_id(key: str) -> str:
    """A stable 12-character public id for a seeded patient.

    Derived from the seed key so it survives a demo reset. Random ids would work equally well for
    the product, but would mean every screenshot and every deep link broke each time the demo was
    reset — which is several times a week.
    """
    digest = seed_uuid("public_id", key).int
    characters = []
    for _ in range(12):
        digest, index = divmod(digest, len(_PUBLIC_ID_ALPHABET))
        characters.append(_PUBLIC_ID_ALPHABET[index])
    return "".join(characters)


# ---------------------------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedUser:
    key: str
    first_name: str
    last_name: str
    email: str
    password: str
    role: UserRole


#: The demo login. Documented in the README, created through the Supabase admin API by the seed.
#:
#: The password is intentionally in source: this account exists only in a local Supabase instance
#: holding synthetic records, and a demo whose credentials are a secret is a demo nobody can run.
DEMO_ADMIN = SeedUser(
    key="alex-morgan",
    first_name="Alex",
    last_name="Morgan",
    email="alex.morgan@greenvalley.example.com",
    password="ClinicRecallDemo2026!",
    role=UserRole.ADMIN,
)

#: A second account, so the ADMIN/STAFF distinction is demonstrable rather than theoretical —
#: the admin-only demo utilities in Settings are invisible to this one.
DEMO_STAFF = SeedUser(
    key="jordan-reyes",
    first_name="Jordan",
    last_name="Reyes",
    email="jordan.reyes@greenvalley.example.com",
    password="ClinicRecallDemo2026!",
    role=UserRole.STAFF,
)

SEED_USERS = (DEMO_ADMIN, DEMO_STAFF)


# ---------------------------------------------------------------------------------------------
# Reminder history
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedReminder:
    """One reminder in a patient's backdated history."""

    rule_key: ReminderRuleKey | None
    """``None`` for a manual send."""

    days_ago: int
    """When it was sent, relative to today."""

    status: ReminderEventStatus = ReminderEventStatus.DELIVERED

    failure_reason: str | None = None


# ---------------------------------------------------------------------------------------------
# The named fixtures (SPEC §7.3)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedFixture:
    """A patient the demo script names by name, in a state it claims exactly."""

    key: str
    first_name: str
    last_name: str
    email: str
    phone: str

    days_until_due: int
    """Negative is overdue. The patient's ``next_annual_due_date`` relative to today."""

    expected_status: PatientStatus
    """Asserted by ``tests/test_seed.py``. This is the contract with the demo script."""

    reminders: tuple[SeedReminder, ...] = ()
    scheduled_in_days: int | None = None
    completed_days_ago: int | None = None
    note: str = ""
    """Why this fixture exists, in the demo's terms."""


NAMED_FIXTURES: tuple[NamedFixture, ...] = (
    NamedFixture(
        key="sarah-johnson",
        first_name="Sarah",
        last_name="Johnson",
        email="sarah.johnson@example.com",
        phone="555-0118",
        days_until_due=-24,
        expected_status=PatientStatus.OVERDUE,
        reminders=(
            SeedReminder(ReminderRuleKey.T_MINUS_30, days_ago=54),
            SeedReminder(ReminderRuleKey.T_MINUS_7, days_ago=31),
            # T_ZERO is deliberately absent.
            #
            # SPEC §7.3 requires it: the live "Send Reminder" beat needs something left to do, and
            # a patient whose reminders had all fired would leave the demo with nothing to press.
            #
            # At 24 days overdue she sits well outside the 3-day catch-up window
            # (app/services/reminders.py), so running the job during a demo cannot backfill it and
            # take the moment away. A test in test_reminder_eligibility.py pins that.
        ),
        note="The demo opens here. Two delivered reminders, T_ZERO left for the live send.",
    ),
    NamedFixture(
        key="michael-brennan",
        first_name="Michael",
        last_name="Brennan",
        email="michael.brennan@example.com",
        phone="555-0134",
        days_until_due=11,
        expected_status=PatientStatus.DUE_SOON,
        reminders=(SeedReminder(ReminderRuleKey.T_MINUS_30, days_ago=19),),
        note="Shows the campaign working ahead of a due date.",
    ),
    NamedFixture(
        key="jennifer-tran",
        first_name="Jennifer",
        last_name="Tran",
        email="jennifer.tran@example.com",
        phone="555-0156",
        days_until_due=0,
        expected_status=PatientStatus.DUE,
        reminders=(SeedReminder(ReminderRuleKey.T_ZERO, days_ago=0),),
        note="Due today, reminded this morning — the system working unattended.",
    ),
    NamedFixture(
        key="david-okafor",
        first_name="David",
        last_name="Okafor",
        email="david.okafor@example.com",
        phone="555-0171",
        days_until_due=-9,
        expected_status=PatientStatus.SCHEDULED,
        # Reminded, then booked. That sequence is what makes him count towards "appointments
        # recovered" — the metric requires a delivered reminder followed by a booking within 30
        # days (SPEC §8), so the revenue figure has to be built from patients whose history
        # actually satisfies it.
        reminders=(SeedReminder(ReminderRuleKey.T_MINUS_7, days_ago=14),),
        scheduled_in_days=9,
        note="The outcome the product exists to produce: reminded, then booked.",
    ),
    NamedFixture(
        key="maria-castillo",
        first_name="Maria",
        last_name="Castillo",
        email="maria.castillo@example.com",
        phone="555-0189",
        # Seen 6 days ago, so the next visit is a full cycle out. 359 rather than 365 because the
        # due date is measured from the visit, not from today.
        days_until_due=359,
        expected_status=PatientStatus.COMPLETED,
        reminders=(SeedReminder(ReminderRuleKey.T_MINUS_7, days_ago=20),),
        completed_days_ago=6,
        note="The loop closed: reminded, seen, and rolled forward a year.",
    ),
    NamedFixture(
        key="robert-hale",
        first_name="Robert",
        last_name="Hale",
        email="robert.hale.bounce@example.com",
        phone="555-0193",
        days_until_due=-16,
        expected_status=PatientStatus.OVERDUE,
        reminders=(
            SeedReminder(
                ReminderRuleKey.T_MINUS_7,
                days_ago=9,
                status=ReminderEventStatus.FAILED,
                failure_reason="Recipient address does not exist (hard bounce).",
            ),
        ),
        # The address contains "bounce", which MockEmailProvider rejects — so re-sending from the
        # failure-recovery screen genuinely fails again until someone corrects the address, which
        # is the point of that screen.
        note="Surfaces the failure-recovery flow. A hard bounce nobody has fixed yet.",
    ),
)


# ---------------------------------------------------------------------------------------------
# The rest of the roster
# ---------------------------------------------------------------------------------------------

#: 55 patients in total (SPEC §7.3), across every status.
TOTAL_PATIENTS = 55

#: The distribution, deliberately uneven.
#:
#: SPEC §7.3 asks for "realistic distribution (not 8 per bucket)", and the reason is that an even
#: split looks generated. A real practice has most patients quietly up to date, a handful due
#: soon, and a worrying tail of overdue — which is precisely the shape that makes the dashboard
#: worth looking at. An even spread would also make the Recall Overview a flat, meaningless bar.
STATUS_DISTRIBUTION: dict[PatientStatus, int] = {
    PatientStatus.ACTIVE: 24,
    PatientStatus.DUE_SOON: 9,
    PatientStatus.OVERDUE: 8,
    PatientStatus.DUE: 5,
    PatientStatus.SCHEDULED: 4,
    PatientStatus.COMPLETED: 3,
    PatientStatus.INACTIVE: 2,
}

assert sum(STATUS_DISTRIBUTION.values()) == TOTAL_PATIENTS, (
    "The distribution must add up to the patient count the demo script quotes."
)

#: Days-until-due ranges that produce each status with a 12-month interval.
#: Mirrors the bands in SPEC §5.2 — see app/services/recall.py.
STATUS_DUE_RANGES: dict[PatientStatus, tuple[int, int]] = {
    PatientStatus.ACTIVE: (45, 340),
    PatientStatus.DUE_SOON: (2, 29),
    PatientStatus.DUE: (-6, -1),
    PatientStatus.OVERDUE: (-95, -9),
    PatientStatus.SCHEDULED: (-40, 20),
    PatientStatus.COMPLETED: (330, 358),
    PatientStatus.INACTIVE: (-60, 200),
}

#: How far back the seeded history reaches (SPEC §7.3: "60-90 days of backdated reminder and
#: activity history so every chart and feed is populated at first login").
HISTORY_WINDOW_DAYS = 90

#: Fictional names only. Deliberately varied, because a patient list of forty Smiths looks like
#: test data and a clinic owner notices.
FIRST_NAMES = (
    "Eleanor",
    "Marcus",
    "Priya",
    "Daniel",
    "Grace",
    "Tomas",
    "Aisha",
    "Peter",
    "Rosa",
    "Henry",
    "Lena",
    "Samuel",
    "Nadia",
    "Oscar",
    "Claire",
    "Malik",
    "Ingrid",
    "Victor",
    "Yuki",
    "Andre",
    "Beatriz",
    "Colin",
    "Delphine",
    "Emeka",
    "Farah",
    "Gabriel",
    "Helena",
    "Ivan",
    "Josefina",
    "Kai",
    "Louise",
    "Mateo",
    "Noor",
    "Otto",
    "Paloma",
    "Quentin",
    "Rafael",
    "Simone",
    "Theo",
    "Ursula",
    "Vikram",
    "Wren",
    "Xiomara",
    "Yusuf",
    "Zara",
    "Adaeze",
    "Bruno",
    "Camille",
    "Dmitri",
    "Esther",
    "Felix",
    "Greta",
    "Hugo",
    "Iris",
)

LAST_NAMES = (
    "Whitfield",
    "Okonkwo",
    "Lindqvist",
    "Marchetti",
    "Delacroix",
    "Nakashima",
    "Alvarado",
    "Fitzgerald",
    "Bergman",
    "Sandoval",
    "Kowalczyk",
    "Abernathy",
    "Villanueva",
    "Thornton",
    "Ibrahim",
    "Petrov",
    "Kaminski",
    "Salazar",
    "Donnelly",
    "Fontaine",
    "Hargreaves",
    "Mbeki",
    "Oyelaran",
    "Rasmussen",
    "Sinclair",
    "Tremblay",
    "Vasquez",
    "Wickham",
    "Zielinski",
    "Achebe",
    "Bianchi",
    "Castellanos",
    "Duarte",
    "Eriksen",
    "Farrow",
    "Gallagher",
    "Hollis",
    "Ishikawa",
    "Jovanovic",
    "Kirkland",
    "Laurent",
    "Moreau",
    "Nilsson",
    "Ortega",
    "Prescott",
    "Quintero",
    "Rivas",
    "Stavros",
    "Tanaka",
    "Underwood",
)


@dataclass
class RosterEntry:
    """A generated (unnamed) patient."""

    key: str
    first_name: str
    last_name: str
    email: str
    phone: str
    days_until_due: int
    expected_status: PatientStatus
    reminders: list[SeedReminder] = field(default_factory=list)
    scheduled_in_days: int | None = None
    opted_out: bool = False
    reminders_paused: bool = False
