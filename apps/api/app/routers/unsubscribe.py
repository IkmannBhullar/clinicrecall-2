"""The one-click unsubscribe endpoint (SPEC §6.5).

This is the only route in the application reached by someone who is not staff. A patient opens it
from a link in an email, on a phone, with no session and no account. So it:

* is authenticated by the signed token in the URL and nothing else;
* returns HTML rather than JSON, because a browser is opening it directly;
* is idempotent, because someone unsure whether it worked will click again;
* says nothing about the patient beyond confirming the change.

Clinics ask about opt-out in every demo, and a product that cannot show one does not get taken
seriously. This is also the honest answer to the second of SPEC §12's three questions.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import AUTH_LIMIT, limiter
from app.core.tokens import InvalidUnsubscribeTokenError, verify_unsubscribe_token
from app.services.reminders import ReminderService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["unsubscribe"])


def _page(*, heading: str, message: str, success: bool) -> HTMLResponse:
    """Render a minimal confirmation page.

    Self-contained by necessity: no stylesheet, no font, no image, no script. This page is opened
    from an email client, frequently on a phone with a poor connection, and every external
    request is one more thing that can fail in front of someone who is trying to withdraw
    consent. The styles are inline for the same reason the reminder email's are.
    """
    accent = "#1f7a5a" if success else "#b42318"

    return HTMLResponse(
        status_code=200 if success else 400,
        content=f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading}</title>
</head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:64px auto;padding:0 16px;">
    <div style="background:#fff;border:1px solid #e4e6ea;border-radius:8px;padding:32px;">
      <h1 style="margin:0 0 12px;font-size:20px;color:{accent};">{heading}</h1>
      <p style="margin:0;font-size:15px;line-height:24px;color:#475467;">{message}</p>
    </div>
    <p style="margin:16px 0 0;text-align:center;font-size:12px;color:#98a2b3;">
      Demo data — synthetic patients only.
    </p>
  </div>
</body>
</html>""",
    )


@router.get(
    "/unsubscribe/{token}",
    response_class=HTMLResponse,
    summary="Stop receiving reminders",
    include_in_schema=False,  # reached from an email, not from the API surface
)
@limiter.limit(AUTH_LIMIT)
def unsubscribe(
    # Required by @limiter.limit — see RATE_LIMITED_ENDPOINT_SIGNATURE in app/core/rate_limit.py.
    request: Request,
    response: Response,
    token: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Verify the signed token and opt the patient out.

    Rate-limited despite being a public convenience: without a limit, the endpoint is an oracle
    for testing forged tokens at speed.
    """
    try:
        public_id = verify_unsubscribe_token(token)
    except InvalidUnsubscribeTokenError:
        # No detail about *why*. Distinguishing "no such patient" from "bad signature" would let
        # someone probing the endpoint learn which identifiers are real.
        logger.info("Rejected an unsubscribe link with an invalid token")
        return _page(
            heading="This link is not valid",
            message=(
                "This unsubscribe link could not be verified. It may have been copied "
                "incompletely from the email. Please contact your clinic directly and they will "
                "remove you from reminders."
            ),
            success=False,
        )

    found = ReminderService(db).record_opt_out_by_public_id(public_id)
    db.commit()

    if not found:
        return _page(
            heading="This link is not valid",
            message=(
                "This unsubscribe link could not be verified. Please contact your clinic "
                "directly and they will remove you from reminders."
            ),
            success=False,
        )

    return _page(
        heading="You have been unsubscribed",
        message=(
            "You will no longer receive annual visit reminders from this clinic. "
            "If you change your mind, or if this was a mistake, please contact the clinic "
            "directly."
        ),
        success=True,
    )
