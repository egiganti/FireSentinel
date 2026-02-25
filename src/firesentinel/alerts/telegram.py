"""Telegram bot client for sending fire alert messages.

Uses httpx for raw Telegram Bot API calls. Handles rate limits (429),
chat not found (400), and bot blocked (403) errors gracefully.

All user-facing text is in Spanish; code and comments are in English.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramAlertClient:
    """Async client for the Telegram Bot API.

    Sends alert messages via the ``sendMessage`` endpoint and supports
    editing existing messages for escalation updates.
    """

    def __init__(self, bot_token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{bot_token}/"
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str = "Markdown",
    ) -> bool:
        """Send a text message to a Telegram chat.

        Args:
            chat_id: Target chat identifier (user, group, or channel).
            text: Message body. Supports Markdown formatting.
            parse_mode: Telegram parse mode (default ``Markdown``).

        Returns:
            ``True`` if the message was delivered, ``False`` otherwise.
        """
        url = f"{self._base_url}sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            response = await self._client.post(url, json=payload)

            if response.status_code == 200:
                logger.info("Telegram message sent to chat_id=%s", chat_id)
                return True

            if response.status_code == 429:
                # Rate-limited -- extract retry_after and wait
                data = response.json()
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                logger.warning(
                    "Telegram rate limited for chat_id=%s, retry_after=%ds",
                    chat_id,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                # Retry once after waiting
                retry_response = await self._client.post(url, json=payload)
                if retry_response.status_code == 200:
                    logger.info("Telegram message sent to chat_id=%s after retry", chat_id)
                    return True
                logger.error(
                    "Telegram retry failed for chat_id=%s, status=%d",
                    chat_id,
                    retry_response.status_code,
                )
                return False

            if response.status_code == 400:
                logger.error(
                    "Telegram chat not found: chat_id=%s, response=%s",
                    chat_id,
                    response.text,
                )
                return False

            if response.status_code == 403:
                logger.error("Telegram bot blocked by user: chat_id=%s", chat_id)
                return False

            logger.error(
                "Telegram send failed: chat_id=%s, status=%d, body=%s",
                chat_id,
                response.status_code,
                response.text,
            )
            return False

        except httpx.HTTPError as exc:
            logger.error("Telegram HTTP error for chat_id=%s: %s", chat_id, exc)
            return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> bool:
        """Edit an existing Telegram message (for escalation updates).

        Args:
            chat_id: Chat containing the message.
            message_id: ID of the message to edit.
            text: New message body.
            parse_mode: Telegram parse mode (default ``Markdown``).

        Returns:
            ``True`` if the edit succeeded, ``False`` otherwise.
        """
        url = f"{self._base_url}editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            response = await self._client.post(url, json=payload)

            if response.status_code == 200:
                logger.info(
                    "Telegram message edited: chat_id=%s, message_id=%d",
                    chat_id,
                    message_id,
                )
                return True

            logger.error(
                "Telegram edit failed: chat_id=%s, message_id=%d, status=%d",
                chat_id,
                message_id,
                response.status_code,
            )
            return False

        except httpx.HTTPError as exc:
            logger.error(
                "Telegram HTTP error editing message: chat_id=%s, %s",
                chat_id,
                exc,
            )
            return False

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
