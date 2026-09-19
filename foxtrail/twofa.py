# -*- coding: utf-8 -*-
"""
Zweitfaktor: TOTP (Authenticator-App) und WebAuthn/Passkey.

TOTP funktioniert ueberall (auch http im LAN). Fuer Passkeys verlangt der Browser einen
"secure context": HTTPS oder http://localhost - eine nackte IP wie 10.0.1.245 lehnt er ab.
passkey_possible() sagt, ob es aktuell geht; sonst zeigt die Oberflaeche nur TOTP.

RP-ID / Origin werden aus X-Forwarded-Host bzw. Host abgeleitet (Port weg, lowercase), damit
die Seite hinter einem Reverse Proxy mit dem oeffentlichen Hostnamen arbeitet.
"""

import base64
import io
import ipaddress
import json

import pyotp
import qrcode
import qrcode.image.svg
from webauthn import (base64url_to_bytes, generate_authentication_options,
                      generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement,
                                      UserVerificationRequirement)

ISSUER = "Foxtrail-Tracker"


# ---- Host / Origin ------------------------------------------------------------ #
def rp_id(request):
    host = (request.headers.get("X-Forwarded-Host")
            or request.headers.get("Host") or request.host or "localhost")
    host = host.split(",")[0].strip().lower()
    if host.startswith("["):                       # IPv6 [::1]:8080
        host = host[1:].split("]")[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host


def _scheme(request):
    return (request.headers.get("X-Forwarded-Proto") or request.scheme or "http").split(",")[0].strip()


def origin(request):
    host = (request.headers.get("X-Forwarded-Host")
            or request.headers.get("Host") or request.host)
    return f"{_scheme(request)}://{host.split(',')[0].strip()}"


def passkey_possible(request):
    rid = rp_id(request)
    try:
        ipaddress.ip_address(rid)
        return False
    except ValueError:
        pass
    return _scheme(request) == "https" or rid in ("localhost", "localhost.localdomain")


# ---- TOTP --------------------------------------------------------------------- #
def new_secret():
    return pyotp.random_base32()


def totp_uri(username, secret):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify_totp(secret, code):
    code = (code or "").strip().replace(" ", "")
    if not (secret and code.isdigit()):
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def qr_svg(data):
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=9, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


# ---- WebAuthn / Passkey ------------------------------------------------------- #
def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _descriptors(passkeys):
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["id"])) for p in passkeys]


def reg_options(user, passkeys, request):
    opts = generate_registration_options(
        rp_id=rp_id(request), rp_name=ISSUER,
        user_name=user["username"], user_display_name=user.get("anzeigename") or user["username"],
        user_id=user["username"].encode("utf-8"),
        exclude_credentials=_descriptors(passkeys),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    return options_to_json(opts), opts.challenge


def reg_verify(request, challenge, response_json):
    v = verify_registration_response(
        credential=response_json, expected_challenge=challenge,
        expected_rp_id=rp_id(request), expected_origin=origin(request),
        require_user_verification=False,
    )
    return {"id": _b64(v.credential_id), "public_key": _b64(v.credential_public_key),
            "sign_count": v.sign_count}


def auth_options(passkeys, request):
    opts = generate_authentication_options(
        rp_id=rp_id(request), allow_credentials=_descriptors(passkeys),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(opts), opts.challenge


def auth_verify(request, challenge, response_json, stored):
    v = verify_authentication_response(
        credential=response_json, expected_challenge=challenge,
        expected_rp_id=rp_id(request), expected_origin=origin(request),
        credential_public_key=base64url_to_bytes(stored["public_key"]),
        credential_current_sign_count=stored.get("sign_count", 0),
        require_user_verification=False,
    )
    return v.new_sign_count


def credential_id_of(response_json):
    """Rohes credential.id (base64url) aus der Browser-Antwort."""
    try:
        return json.loads(response_json)["id"]
    except (ValueError, KeyError, TypeError):
        return None
