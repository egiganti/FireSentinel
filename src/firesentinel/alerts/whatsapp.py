"""WhatsApp alert client using the Twilio REST API.

Uses httpx with Basic authentication for raw Twilio API calls.
Handles errors gracefully and logs all send attempts.

All user-facing text is in Spanish; code and comments are in English.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class WhatsAppAlertClient:
    """Async client for sending WhatsApp messages via Twilio.

    Messages are sent through the Twilio Messages resource using
    ``whatsapp:`` prefixed phone numbers.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> None:
        self._account_sid = account_sid
        self._from_number = from_number
        self._url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        self._client = httpx.AsyncClient(
            timeout=30.0,
            auth=(account_sid, auth_token),
        )

    async def send_message(self, to_number: str, text: str) -> bool:
        """Send a WhatsApp message via Twilio.

        Args:
            to_number: Recipient phone number (without ``whatsapp:`` prefix).
            text: Plain-text message body.

        Returns:
            ``True`` if Twilio accepted the message, ``False`` otherwise.
        """
        form_data = {
            "From": f"whatsapp:{self._from_number}",
            "To": f"whatsapp:{to_number}",
            "Body": text,
        }

        try:
            response = await self._client.post(self._url, data=form_data)

            if response.status_code == 201:
                logger.info("WhatsApp message sent to %s", to_number)
                return True

            logger.error(
                "WhatsApp send failed: to=%s, status=%d, body=%s",
                to_number,
                response.status_code,
                response.text,
            )
            return False

        except httpx.HTTPError as exc:
            logger.error("WhatsApp HTTP error for to=%s: %s", to_number, exc)
            return False

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
