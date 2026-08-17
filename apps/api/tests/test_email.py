"""The email provider, the rendered message, and the unsubscribe token (SPEC §6.4, §6.5).

The content assertions here are not stylistic. SPEC §6.5 forbids interpolating any clinical
information into a reminder, and that rule has a real reason behind it: the message goes to an
address the practice cannot vouch for, may be read on a shared screen or a lock-screen preview,
and sits in an inbox indefinitely. "It may be time to book your annual visit" is the whole
permitted message.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.tokens import (
    InvalidUnsubscribeTokenError,
    make_unsubscribe_token,
    make_unsubscribe_url,
    verify_job_token,
    verify_unsubscribe_token,
)
from app.email.provider import (
    MockEmailProvider,
    OutboundEmail,
    set_email_provider,
)
from app.email.renderer import build_subject, render_reminder
from app.models.clinic_settings import ClinicSettings
from app.models.organization import Organization
from app.repositories.patients import PatientRepository
from tests.conftest import make_patient


@pytest.fixture
def provider() -> Iterator[MockEmailProvider]:
    mock = MockEmailProvider(delivery_delay_seconds=0.0)
    set_email_provider(mock)
    yield mock
    set_email_provider(None)


@pytest.fixture
def rendered(
    db: Session, organization: Organization, clinic_settings: ClinicSettings
) -> OutboundEmail:
    """A reminder rendered for a fully configured practice."""
    clinic_settings.phone = "555-0142"
    clinic_settings.website = "https://greenvalley.example.com"
    clinic_settings.scheduling_url = "https://greenvalley.example.com/book"
    clinic_settings.reminder_signature = "Warm regards,\nThe Green Valley team"

    patient = PatientRepository(db).create(
        organization.id,
        make_patient(
            organization.id,
            first_name="Sarah",
            last_name="Johnson",
            email="sarah.johnson@example.com",
        ),
    )
    db.flush()

    return render_reminder(patient, clinic_settings)


# ---------------------------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------------------------


def test_the_message_carries_the_wording_the_spec_gives(rendered: OutboundEmail) -> None:
    for body in (rendered.html_body, rendered.text_body):
        assert "Hi Sarah" in body
        assert "Green Valley Family Clinic" in body
        assert "annual visit" in body


def test_both_an_html_and_a_text_part_are_produced(rendered: OutboundEmail) -> None:
    """A message with no text alternative scores worse with spam filters — which for a recall
    email means it does not arrive, and the patient does not come in."""
    assert rendered.html_body.strip()
    assert rendered.text_body.strip()
    assert "<html" in rendered.html_body.lower()
    assert "<html" not in rendered.text_body.lower()


def test_the_message_contains_nothing_clinical(rendered: OutboundEmail) -> None:
    """SPEC §6.5: "Never interpolate diagnoses, conditions, or visit reasons."

    Checked as a test rather than trusted to review, because this is the kind of copy that gets
    "improved" later by someone adding a helpful detail.
    """
    forbidden = [
        "diagnos",
        "condition",
        "prescription",
        "medication",
        "symptom",
        "treatment",
        "test result",
        "lab ",
        "referral",
    ]

    for body in (rendered.html_body, rendered.text_body, rendered.subject):
        lowered = body.lower()
        for term in forbidden:
            assert term not in lowered, f"clinical term {term!r} appeared in a reminder"


def test_the_subject_is_unremarkable() -> None:
    """No urgency, no capitals, no marketing. This is a note from a doctor's office."""
    subject = build_subject("Green Valley Family Clinic")

    assert subject == "A reminder from Green Valley Family Clinic about your annual visit"
    assert "!" not in subject
    assert subject != subject.upper()


def test_the_sender_name_is_the_practice_not_the_product(rendered: OutboundEmail) -> None:
    """The patient has a relationship with their clinic and none at all with ClinicRecall.

    An email from an unfamiliar sender is deleted, or reported as spam.
    """
    assert rendered.from_name == "Green Valley Family Clinic"
    assert "ClinicRecall" not in rendered.from_name


def test_the_email_loads_nothing_from_the_network(rendered: OutboundEmail) -> None:
    """SPEC constraint D2, and also plain good email practice.

    Most mail clients block remote content by default, so a design that depends on an image or a
    web font arrives broken for most recipients.
    """
    assert "<img" not in rendered.html_body.lower()
    assert "@import" not in rendered.html_body
    assert "<script" not in rendered.html_body.lower()

    # The only links permitted are the practice's own and the unsubscribe endpoint.
    for url in re.findall(r'href="([^"]+)"', rendered.html_body):
        assert url.startswith(("https://greenvalley.example.com", "tel:", "http://127.0.0.1")), url


def test_a_half_configured_practice_still_renders(db: Session, organization: Organization) -> None:
    """No settings row yet — the email is plainer, not an exception.

    A practice mid-setup should get a simpler reminder rather than a 500.
    """
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    db.flush()

    message = render_reminder(patient, None)

    assert message.subject
    assert "Green Valley Family Clinic" in message.html_body
    assert "Schedule Appointment" not in message.html_body  # no scheduling_url configured


# ---------------------------------------------------------------------------------------------
# The unsubscribe link (SPEC §6.5)
# ---------------------------------------------------------------------------------------------


def test_every_reminder_carries_an_unsubscribe_link(rendered: OutboundEmail) -> None:
    """Required by SPEC §6.5, and asked about in every clinic demo."""
    assert rendered.unsubscribe_url
    assert rendered.unsubscribe_url in rendered.html_body
    assert rendered.unsubscribe_url in rendered.text_body


def test_an_unsubscribe_token_round_trips() -> None:
    token = make_unsubscribe_token("ABCD1234EFGH")

    assert verify_unsubscribe_token(token) == "ABCD1234EFGH"


def test_a_token_cannot_be_forged_by_guessing_a_patient_id() -> None:
    """The whole reason the link is signed.

    Without a signature, anyone could walk the identifier space and opt out a clinic's entire
    recall list — and since opt-out is honoured permanently, that is a quiet and hard-to-notice
    denial of service against the product's core function.
    """
    with pytest.raises(InvalidUnsubscribeTokenError):
        verify_unsubscribe_token("ABCD1234EFGH.notarealsignature")

    with pytest.raises(InvalidUnsubscribeTokenError):
        verify_unsubscribe_token("ABCD1234EFGH")


@pytest.mark.parametrize("token", ["", ".", "x.", ".y", "no-dot-at-all"])
def test_malformed_tokens_are_refused_cleanly(token: str) -> None:
    with pytest.raises(InvalidUnsubscribeTokenError):
        verify_unsubscribe_token(token)


def test_one_patients_signature_does_not_work_for_another() -> None:
    """A signature is over a specific identifier, so it cannot be transplanted."""
    stolen = make_unsubscribe_token("PATIENT00001").split(".")[1]

    with pytest.raises(InvalidUnsubscribeTokenError):
        verify_unsubscribe_token(f"PATIENT00002.{stolen}")


def test_the_unsubscribe_url_is_absolute() -> None:
    """It appears in an email, where a relative path means nothing."""
    url = make_unsubscribe_url("ABCD1234EFGH")

    assert url.startswith("http")
    assert "/unsubscribe/" in url


# ---------------------------------------------------------------------------------------------
# The job token (SPEC §6.3)
# ---------------------------------------------------------------------------------------------


def test_the_job_token_accepts_only_the_configured_value() -> None:
    from app.core.config import settings

    assert verify_job_token(settings.job_token)
    assert not verify_job_token("wrong")
    assert not verify_job_token(None)
    assert not verify_job_token("")


def test_a_prefix_of_the_job_token_is_rejected() -> None:
    """Guards against a comparison that stops at the first difference.

    A `==` on a secret returns as soon as two bytes differ, so the time it takes leaks how much
    of a guess was right — enough to recover the token one byte at a time.
    """
    from app.core.config import settings

    assert not verify_job_token(settings.job_token[:-1])
    assert not verify_job_token(settings.job_token + "x")


# ---------------------------------------------------------------------------------------------
# The mock provider (SPEC §6.4)
# ---------------------------------------------------------------------------------------------


def test_the_mock_accepts_an_ordinary_address(
    provider: MockEmailProvider, rendered: OutboundEmail
) -> None:
    result = provider.send(rendered)

    assert result.accepted
    assert result.provider_message_id
    assert provider.message_count == 1


def test_the_mock_records_what_it_sent(
    provider: MockEmailProvider, rendered: OutboundEmail
) -> None:
    """Which is what lets the UI show the exact email that went out."""
    provider.send(rendered)
    recorded = provider.last_message()

    assert recorded is not None
    assert recorded.message.subject == rendered.subject
    assert recorded.message.html_body == rendered.html_body


def test_the_mock_simulates_a_hard_bounce(provider: MockEmailProvider) -> None:
    """Makes the failure-recovery flow demonstrable without breaking real data.

    SPEC §7.3 needs a patient in a FAILED state, and "Send Test Reminder" should be able to
    exercise the error path deliberately.
    """
    message = OutboundEmail(
        to_address="bounce@example.com",
        to_name="Robert Hale",
        subject="s",
        html_body="<p>h</p>",
        text_body="t",
        from_address="reminders@example.com",
        from_name="Green Valley Family Clinic",
        unsubscribe_url="http://127.0.0.1:8000/unsubscribe/x.y",
    )

    result = provider.send(message)

    assert not result.accepted
    assert result.failure_reason
    # Readable by someone who is not an engineer — this text is shown to clinic staff.
    assert "550" not in result.failure_reason
    assert "does not exist" in result.failure_reason.lower()


def test_provider_message_ids_are_unique(
    provider: MockEmailProvider, rendered: OutboundEmail
) -> None:
    ids = {provider.send(rendered).provider_message_id for _ in range(20)}

    assert len(ids) == 20


# ---------------------------------------------------------------------------------------------
# Text-part layout
# ---------------------------------------------------------------------------------------------


def test_the_text_part_is_readably_spaced(rendered: OutboundEmail) -> None:
    """Guards a real bug found by rendering the email and looking at it.

    Jinja's trim_blocks/lstrip_blocks consume the newline after every block tag, so an earlier
    version of the template produced a run-on message whose footer read
    "Green Valley Family Clinic555-0142https://greenvalley.example.com" — every test still
    passed, because each fragment was present and no assertion looked at the spacing.

    Reading the output is the only way to catch that, so these assertions encode what reading it
    showed.
    """
    text = rendered.text_body

    # The footer fields are on separate lines, not concatenated.
    assert "Green Valley Family Clinic\n555-0142" in text
    assert "555-0142https" not in text

    # Paragraphs are separated by a blank line.
    assert "\n\nThis is a friendly reminder" in text
    assert "\n\nSchedule an appointment:" in text

    # And no run of three or more blank lines, which is the other direction of the same bug.
    assert "\n\n\n\n" not in text


def test_the_text_part_degrades_cleanly_for_a_minimal_practice(
    db: Session, organization: Organization
) -> None:
    """A practice with no phone, scheduling link, or signature still gets a tidy email."""
    patient = PatientRepository(db).create(organization.id, make_patient(organization.id))
    db.flush()

    text = render_reminder(patient, ClinicSettings(clinic_name="Riverside Medical Group")).text_body

    assert "Please contact our office to book a time" in text
    assert "Schedule an appointment:" not in text
    assert "\n\n\n\n" not in text
    # No stray "None" from an unset optional field.
    assert "None" not in text
