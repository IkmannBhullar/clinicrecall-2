"""Renders the reminder email from a patient and their practice's settings.

One function, ``render_reminder``, and one strict rule about what may go into it.

**Nothing clinical, ever.** SPEC §6.5: "Never interpolate diagnoses, conditions, or visit
reasons." The context this builder assembles is the enforcement of that rule — it is a fixed set
of fields, and a diagnosis is not one of them. A recall email travels to an address the practice
cannot vouch for, may be read on a shared screen, and sits in an inbox indefinitely.

The message says one thing: it may be time to book your annual visit.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.tokens import make_unsubscribe_url
from app.email.provider import OutboundEmail
from app.models.clinic_settings import ClinicSettings
from app.models.patient import Patient

TEMPLATE_DIR = Path(__file__).parent / "templates"

# autoescape is on for HTML. A patient called "Sarah <script>" is unlikely but a patient called
# "O'Brien & Sons Clinic" is not, and unescaped ampersands break HTML mail in subtle ways.
#
# trim_blocks/lstrip_blocks keep the plain-text template readable: without them, every `{% if %}`
# leaves a stray blank line in the output, and the text email arrives looking broken.
_environment = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "j2"], default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


def build_subject(clinic_name: str) -> str:
    """The subject line.

    Deliberately unremarkable. No urgency, no capitals, no "ACTION REQUIRED" — this is a
    reminder from a doctor's office, and anything that reads like marketing both erodes trust and
    scores worse with spam filters.
    """
    return f"A reminder from {clinic_name} about your annual visit"


def render_reminder(
    patient: Patient,
    clinic_settings: ClinicSettings | None,
    *,
    template_id: str = "annual_recall",
) -> OutboundEmail:
    """Render a reminder for one patient into a ready-to-send message.

    ``clinic_settings`` may be ``None`` for a practice that has not finished setup. The email
    still renders, using the organization's name and omitting the optional sections, because a
    half-configured practice should get a plainer email rather than an exception.
    """
    clinic_name = (
        clinic_settings.clinic_name if clinic_settings is not None else patient.organization.name
    )

    # The complete set of values a reminder may contain. Adding to this list is the moment to ask
    # whether the new field is clinical — because if it is, it does not belong in an email.
    context = {
        "first_name": patient.first_name,
        "clinic_name": clinic_name,
        "clinic_phone": clinic_settings.phone if clinic_settings else None,
        "clinic_website": clinic_settings.website if clinic_settings else None,
        "scheduling_url": clinic_settings.scheduling_url if clinic_settings else None,
        "reminder_signature": clinic_settings.reminder_signature if clinic_settings else None,
        "unsubscribe_url": make_unsubscribe_url(patient.public_id),
        "subject": build_subject(clinic_name),
    }

    html_body = _environment.get_template(f"{template_id}.html.j2").render(**context)
    text_body = _environment.get_template(f"{template_id}.txt.j2").render(**context)

    return OutboundEmail(
        to_address=patient.email,
        to_name=patient.full_name,
        subject=context["subject"],  # type: ignore[arg-type]
        html_body=html_body,
        text_body=text_body,
        from_address=settings.email_from_address,
        # The practice's name in the From line, not the product's. The patient has a relationship
        # with their clinic and none at all with ClinicRecall; an email from an unfamiliar sender
        # is deleted or reported.
        from_name=clinic_name,
        unsubscribe_url=str(context["unsubscribe_url"]),
    )
