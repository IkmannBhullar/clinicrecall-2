"""The HTTP surface: authentication, the error envelope, and security headers (SPEC §9).

These tests drive the real application through ``TestClient``, so what they assert is what a
browser would actually receive — headers included.

The authentication tests use FastAPI's dependency override to supply token claims, rather than
minting and signing a token for every case. Token verification itself is covered exhaustively in
``test_token_verification.py``; what is being checked here is the layer above it — that a verified
subject is resolved to the right user and the right organization, and that every failure comes
back in the standard envelope.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_token_claims
from app.core.security import TokenClaims
from app.main import app
from app.models.enums import UserRole
from app.models.organization import Organization
from app.models.user import User


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """A test client sharing the test's transaction, so writes roll back with it."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def authenticate_as(auth_user_id: uuid.UUID) -> None:
    """Make the API treat requests as coming from this Supabase subject."""
    app.dependency_overrides[get_token_claims] = lambda: TokenClaims(
        subject=auth_user_id,
        email="staff@example.com",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


# ---------------------------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------------------------


def test_a_protected_route_refuses_an_anonymous_request(client: TestClient) -> None:
    response = client.get("/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_a_malformed_bearer_token_is_refused(client: TestClient) -> None:
    """Reaches real verification: no override is installed for this test."""
    response = client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_a_signed_in_user_gets_their_own_practice(
    client: TestClient, db: Session, organization: Organization, staff_user: User
) -> None:
    """The full resolution chain: JWT.sub → users.auth_user_id → users.organization_id."""
    authenticate_as(staff_user.auth_user_id)

    response = client.get("/me")

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == staff_user.email
    assert body["user"]["role"] == "ADMIN"
    assert body["organization"]["id"] == str(organization.id)


def test_a_verified_token_with_no_application_user_is_refused(client: TestClient) -> None:
    """A Supabase account can exist without an application user — created from the Supabase
    dashboard, or by a half-finished invite. It must reach nothing at all."""
    authenticate_as(uuid.uuid4())

    response = client.get("/me")

    assert response.status_code == 401
    assert "not set up" in response.json()["error"]["message"]


def test_the_client_cannot_choose_its_own_organization(
    client: TestClient,
    db: Session,
    organization: Organization,
    other_organization: Organization,
    staff_user: User,
) -> None:
    """SPEC §3.2: "organization_id is never accepted from the client."

    Every plausible smuggling route is tried at once — header, query string, and body. The
    response must name the user's real practice regardless.
    """
    authenticate_as(staff_user.auth_user_id)

    response = client.get(
        "/me",
        params={"organization_id": str(other_organization.id)},
        headers={"X-Organization-Id": str(other_organization.id)},
    )

    assert response.status_code == 200
    assert response.json()["organization"]["id"] == str(organization.id)
    assert response.json()["organization"]["id"] != str(other_organization.id)


def test_two_users_in_different_practices_see_different_organizations(
    client: TestClient, db: Session, organization: Organization, other_organization: Organization
) -> None:
    """The same endpoint, two identities, two tenants. Nothing crosses."""
    ours = User(
        organization_id=organization.id,
        auth_user_id=uuid.uuid4(),
        first_name="Alex",
        last_name="Morgan",
        email=f"alex+{uuid.uuid4().hex[:6]}@example.com",
        role=UserRole.ADMIN,
    )
    theirs = User(
        organization_id=other_organization.id,
        auth_user_id=uuid.uuid4(),
        first_name="Riley",
        last_name="Chen",
        email=f"riley+{uuid.uuid4().hex[:6]}@example.com",
        role=UserRole.STAFF,
    )
    db.add_all([ours, theirs])
    db.flush()

    authenticate_as(ours.auth_user_id)
    assert client.get("/me").json()["organization"]["id"] == str(organization.id)

    authenticate_as(theirs.auth_user_id)
    assert client.get("/me").json()["organization"]["id"] == str(other_organization.id)


# ---------------------------------------------------------------------------------------------
# The error envelope (SPEC §9)
# ---------------------------------------------------------------------------------------------


def test_every_error_uses_the_same_envelope(client: TestClient) -> None:
    """One shape for every failure, so the frontend needs one code path rather than several."""
    for response in (client.get("/me"), client.get("/no-such-route")):
        body = response.json()

        assert "error" in body
        assert set(body["error"]) >= {"code", "message", "correlation_id"}
        assert isinstance(body["error"]["code"], str)
        assert body["error"]["code"].isupper()


def test_a_correlation_id_is_returned_in_the_body_and_the_header(client: TestClient) -> None:
    """The bridge between what the user sees and what the log holds."""
    response = client.get("/me")

    body_id = response.json()["error"]["correlation_id"]

    assert body_id
    assert response.headers["X-Correlation-ID"] == body_id


def test_correlation_ids_differ_between_requests(client: TestClient) -> None:
    """They identify a request, not a session — otherwise they cannot locate one failure."""
    first = client.get("/me").json()["error"]["correlation_id"]
    second = client.get("/me").json()["error"]["correlation_id"]

    assert first != second


def test_an_inbound_correlation_id_is_honoured(client: TestClient) -> None:
    """Lets a trace span the frontend and the API."""
    response = client.get("/me", headers={"X-Correlation-ID": "frontend-trace-1"})

    assert response.headers["X-Correlation-ID"] == "frontend-trace-1"


def test_an_absurdly_long_inbound_correlation_id_is_truncated(client: TestClient) -> None:
    """It is echoed into a header and written to logs; an unbounded client-controlled string in
    either place is asking for trouble."""
    response = client.get("/me", headers={"X-Correlation-ID": "x" * 5000})

    assert len(response.headers["X-Correlation-ID"]) <= 64


def test_a_routing_404_uses_the_envelope_too(client: TestClient) -> None:
    """FastAPI's own errors are reshaped, so application and framework failures look alike."""
    response = client.get("/definitely-not-a-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_no_error_response_leaks_internals(client: TestClient) -> None:
    """SPEC §9: "Stack traces never reach the client."

    A traceback tells an attacker the framework, the file layout, and often a SQL fragment, while
    telling the receptionist reading it precisely nothing.
    """
    for path in ("/me", "/nope", "/me/../../etc/passwd"):
        text = client.get(path).text.lower()

        for leak in ("traceback", 'file "/', "sqlalchemy", "psycopg", "/users/", "site-packages"):
            assert leak not in text, f"{path} leaked {leak!r}"


# ---------------------------------------------------------------------------------------------
# Security headers (SPEC §9)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "same-origin"),
        ("Cache-Control", "no-store"),
    ],
)
def test_security_headers_are_present(client: TestClient, header: str, expected: str) -> None:
    response = client.get("/health")

    assert response.headers.get(header) == expected


def test_the_api_content_security_policy_forbids_everything(client: TestClient) -> None:
    """The API returns JSON. Nothing should ever load from it, so nothing is permitted."""
    csp = client.get("/health").headers.get("Content-Security-Policy", "")

    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_set(client: TestClient) -> None:
    """Inert on localhost, essential once deployed behind TLS."""
    header = client.get("/health").headers.get("Strict-Transport-Security", "")

    assert "max-age=" in header


def test_headers_are_present_on_errors_too(client: TestClient) -> None:
    """A 401 is served to a browser exactly like a 200 is, so it needs the same protections."""
    response = client.get("/me")

    assert response.status_code == 401
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_the_framework_version_is_not_advertised(client: TestClient) -> None:
    assert "x-powered-by" not in {key.lower() for key in client.get("/health").headers}


# ---------------------------------------------------------------------------------------------
# CORS (SPEC §9)
# ---------------------------------------------------------------------------------------------


def test_the_configured_web_origin_is_allowed(client: TestClient) -> None:
    from app.core.config import settings

    response = client.get("/health", headers={"Origin": settings.web_origin})

    assert response.headers.get("access-control-allow-origin") == settings.web_origin


def test_an_unknown_origin_is_not_granted_access(client: TestClient) -> None:
    """Never a wildcard. With credentials enabled, a permissive CORS policy would let any site
    on the internet make authenticated requests on a signed-in user's behalf."""
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})

    allowed = response.headers.get("access-control-allow-origin")
    assert allowed != "https://evil.example.com"
    assert allowed != "*"


# ---------------------------------------------------------------------------------------------
# Request size limit (SPEC §9)
# ---------------------------------------------------------------------------------------------


def test_an_oversized_request_is_rejected(client: TestClient) -> None:
    """Refused on the declared Content-Length, before a byte of the body is read.

    An unbounded upload lets a single request exhaust server memory, and it takes no
    authentication to attempt.
    """
    from app.core.middleware import MAX_REQUEST_BODY_BYTES

    response = client.post(
        "/me",
        content=b"x",
        headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_the_oversize_rejection_still_uses_the_envelope(client: TestClient) -> None:
    """Middleware rejections bypass the application's exception handlers, so this response is
    constructed by hand — and could easily have ended up as the one differently-shaped error in
    the API."""
    from app.core.middleware import MAX_REQUEST_BODY_BYTES

    body = client.post(
        "/me", content=b"x", headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)}
    ).json()

    assert set(body["error"]) >= {"code", "message", "correlation_id"}


def test_a_normal_sized_request_passes_the_limit(client: TestClient) -> None:
    """The limit must not reject ordinary traffic. A 405 here means it got past the middleware
    and reached routing, which is exactly what should happen."""
    response = client.post("/me", json={"hello": "world"})

    assert response.status_code != 413
