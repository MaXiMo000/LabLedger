"""Sending mail through EmailJS, from the server and never the browser.

EmailJS is normally a client-side service: the page posts to their API with a
*public* key, and they send the mail. That model is fine for a contact form and
wrong for everything here. The public key is readable in any browser's devtools,
so anyone could send mail through the account's Gmail using its templates — and,
far worse, a password-reset link composed in a browser is worthless, because
whoever controls the browser can mint one for any address they like.

The private key enables EmailJS's strict, server-side API. The token is created
here, the link is composed here, and the recipient's browser is handed nothing.

**A failed send never fails the operation it belongs to.** The invitation still
exists and its link is still returned; the password reset still answers exactly
as it would have. Losing an email is bad, but a 500 on the reset endpoint tells
an attacker which addresses are registered, and an invitation that half-exists
is worse than one whose email has to be resent.

**Nothing sensitive is logged.** A reset link in a log file is a working key to
an account, so failures record the template and the outcome and nothing else —
the same rule the application logs already follow for filenames.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger("labledger.mail")

ENDPOINT = "https://api.emailjs.com/api/v1.0/email/send"
TIMEOUT = 10.0


async def send(template_id: str | None, params: dict) -> bool:
    """Send one templated email. Returns whether it went.

    Never raises. Callers are in the middle of something that has already
    succeeded — a grant issued, a token stored — and unwinding that because a
    third party was slow would be the wrong trade.
    """
    if not settings.mail_configured or not template_id:
        # Not an error: a dev machine without keys should still run the flow
        # end to end, minus the email.
        logger.info("mail skipped (not configured) template=%s", template_id)
        return False

    payload = {
        "service_id": settings.emailjs_service_id,
        "template_id": template_id,
        "user_id": settings.emailjs_public_key,
        "accessToken": settings.emailjs_private_key,
        "template_params": params,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(ENDPOINT, json=payload)
        if r.status_code == 200:
            logger.info("mail sent template=%s", template_id)
            return True
        # Body, not just status: EmailJS returns 400 with a readable reason
        # (bad template id, unconfigured service) that is worth having. It
        # echoes no recipient data.
        logger.error("mail failed template=%s status=%s body=%s",
                     template_id, r.status_code, r.text[:200])
    except httpx.HTTPError as exc:
        logger.error("mail failed template=%s %s", template_id, type(exc).__name__)
    return False


async def send_password_reset(to_email: str, reset_url: str) -> bool:
    """Send the link that lets somebody back into their account."""
    return await send(settings.emailjs_template_reset, {
        "to_email": to_email,
        "reset_url": reset_url,
        "expires_minutes": settings.password_reset_ttl_min,
    })


async def send_invitation(
    to_email: str, inviter_email: str, role: str, invite_url: str, expires_on: str
) -> bool:
    """Send an offer of access to a record.

    The patient's name is deliberately absent. It would put clinical identity
    through EmailJS and Gmail — neither of which this project has an agreement
    with — and into a mailbox that may be shared or forwarded. The recipient
    learns whose record it is after they authenticate, which is already how the
    invitation page behaves.
    """
    return await send(settings.emailjs_template_invite, {
        "to_email": to_email,
        "inviter_email": inviter_email,
        "role": role,
        "invite_url": invite_url,
        "expires_on": expires_on,
    })
