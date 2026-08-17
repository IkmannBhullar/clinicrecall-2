"""Email delivery providers (SPEC §6.4).

    class EmailProvider(Protocol):
        def send(self, message: OutboundEmail) -> SendResult: ...

``MockEmailProvider`` is the default and is **fully functional**, not a stub. It renders the real
message, stores it so the UI can show exactly what went out, returns a synthetic provider message
id, and reports a delivery confirmation after a short delay so the interface shows a realistic
SENT → DELIVERED transition.

That completeness is a requirement rather than a convenience. SPEC constraint D2 forbids any
runtime network access, and the whole product has to be demonstrable with zero paid services —
so the mock is the path the demo actually runs on, and anything it fakes badly is something the
demo shows badly.

A real provider (Resend, SES) can sit behind ``EMAIL_PROVIDER=`` later. The protocol is
deliberately tiny so that adding one is a single file with no changes anywhere else.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

#: How long the mock provider waits before reporting delivery.
#:
#: Long enough that a person watching the screen sees "Sent" become "Delivered" rather than the
#: badge simply appearing in its final state — which is the difference between the demo looking
#: alive and looking pre-baked. Short enough not to be a pause anyone waits through.
MOCK_DELIVERY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class OutboundEmail:
    """A rendered message, ready to hand to a provider.

    Immutable and fully rendered: by the time an ``OutboundEmail`` exists, every decision about
    content has been made. A provider's job is transport, and it has no business consulting a
    template or a patient record.
    """

    to_address: str
    to_name: str
    subject: str
    html_body: str
    text_body: str
    from_address: str
    from_name: str

    #: Where the one-click unsubscribe link points. Carried separately as well as being inside
    #: the body so a real provider can also set the RFC 8058 List-Unsubscribe header.
    unsubscribe_url: str


@dataclass(frozen=True)
class SendResult:
    """What a provider reports back."""

    accepted: bool

    #: The provider's own identifier for the message. Stored so a delivery question can be traced
    #: to a specific send.
    provider_message_id: str | None = None

    #: Plain-English reason for a rejection, shown to staff in the failure-recovery flow. Must be
    #: readable by someone who is not an engineer: "Recipient address does not exist", not
    #: "SMTP 550 5.1.1".
    failure_reason: str | None = None

    #: Seconds until this message should be considered delivered. ``None`` means the provider
    #: will not confirm delivery, so the event stays SENT.
    deliver_after_seconds: float | None = None


class EmailProvider(Protocol):
    """The interface every provider implements (SPEC §6.4)."""

    def send(self, message: OutboundEmail) -> SendResult:
        """Attempt to deliver one message.

        Must not raise for an ordinary delivery failure — a bad address is a ``SendResult`` with
        ``accepted=False``, because it is information about a patient's record rather than an
        error in the program. Exceptions are reserved for the provider itself being broken.
        """
        ...


@dataclass
class RecordedMessage:
    """A message the mock "sent", kept in memory for inspection."""

    message: OutboundEmail
    provider_message_id: str
    sent_at: datetime
    accepted: bool
    failure_reason: str | None = None


class MockEmailProvider:
    """The default provider. Fully functional, entirely local.

    Keeps every message it is given, so tests can assert on exactly what was sent and the
    Reminders page can show the rendered email without re-rendering it from a template that may
    since have changed.

    **Simulated failures.** Any address whose local part contains ``bounce`` is rejected as a hard
    bounce. That is what makes the failure-recovery flow demonstrable (SPEC §7.3's Robert Hale)
    and lets "Send Test Reminder" exercise the error path deliberately — without anybody needing
    to break real data to see what a failure looks like.
    """

    def __init__(self, *, delivery_delay_seconds: float = MOCK_DELIVERY_DELAY_SECONDS) -> None:
        self.delivery_delay_seconds = delivery_delay_seconds
        self.sent_messages: list[RecordedMessage] = []

    def send(self, message: OutboundEmail) -> SendResult:
        local_part = message.to_address.split("@", 1)[0].lower()
        provider_message_id = f"mock-{uuid.uuid4().hex[:16]}"

        if "bounce" in local_part:
            result = SendResult(
                accepted=False,
                provider_message_id=provider_message_id,
                failure_reason="Recipient address does not exist (hard bounce).",
            )
        else:
            result = SendResult(
                accepted=True,
                provider_message_id=provider_message_id,
                deliver_after_seconds=self.delivery_delay_seconds,
            )

        self.sent_messages.append(
            RecordedMessage(
                message=message,
                provider_message_id=provider_message_id,
                sent_at=datetime.now(UTC),
                accepted=result.accepted,
                failure_reason=result.failure_reason,
            )
        )

        # The address is never logged — that is patient contact information (SPEC §9). The
        # redaction filter would catch it, but relying on a safety net to clean up a deliberate
        # leak is not a trade worth making.
        logger.info(
            "MockEmailProvider %s message %s",
            "accepted" if result.accepted else "rejected",
            provider_message_id,
        )

        return result

    def clear(self) -> None:
        """Forget everything sent so far. Used between tests."""
        self.sent_messages = []

    @property
    def message_count(self) -> int:
        return len(self.sent_messages)

    def last_message(self) -> RecordedMessage | None:
        return self.sent_messages[-1] if self.sent_messages else None


# ---------------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------------

# One instance per process, so the mock's record of what it sent survives across requests. That
# is what lets the UI display the exact email that went out during this session.
_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """Return the configured provider, creating it on first use."""
    global _provider

    if _provider is None:
        if settings.email_provider == "mock":
            _provider = MockEmailProvider()
        else:
            # Real providers arrive only if someone needs them. Failing clearly beats silently
            # falling back to the mock, which would look like everything worked while no patient
            # received anything.
            raise NotImplementedError(
                f"EMAIL_PROVIDER={settings.email_provider!r} is not implemented. "
                "The application is fully functional with EMAIL_PROVIDER=mock, which is the "
                "default and requires no external service."
            )

    return _provider


def set_email_provider(provider: EmailProvider | None) -> None:
    """Replace the process-wide provider. Used by tests and by the seed."""
    global _provider
    _provider = provider
