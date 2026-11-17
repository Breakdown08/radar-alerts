from typing import Protocol

import httpx


class TelegramError(Exception):
    retryable: bool
    retry_after: float | None

    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable: bool = retryable
        self.retry_after: float | None = retry_after


class Telegram(Protocol):
    def send_message(self, chat_id: int, text: str) -> int: ...
    def get_updates(self, offset: int, timeout: int) -> list[dict[str, object]]: ...


SAFE_TRANSPORT_ERRORS: tuple[type[httpx.TransportError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


class HttpTelegram:
    def __init__(self, http: httpx.Client, token: str, base_url: str = "https://api.telegram.org") -> None:
        self._http: httpx.Client = http
        self._url: str = f"{base_url.rstrip('/')}/bot{token}"

    def _call(self, method: str, payload: dict[str, object]) -> object:
        """POST one Bot API method and return result; the URL (with the token) never reaches an error."""
        try:
            response: httpx.Response = self._http.post(f"{self._url}/{method}", json=payload)
        except httpx.TransportError as exc:
            raise TelegramError(
                f"request to Telegram failed: {type(exc).__name__}",
                retryable=isinstance(exc, SAFE_TRANSPORT_ERRORS),
            ) from None
        except httpx.HTTPError as exc:
            raise TelegramError(
                f"request to Telegram failed: {type(exc).__name__}", retryable=False
            ) from None
        body: object
        try:
            body = response.json()
        except ValueError:
            body = None
        description: str = ""
        retry_after: float | None = None
        if isinstance(body, dict):
            text: object = body.get("description")
            description = f": {text}" if isinstance(text, str) else ""
            parameters: object = body.get("parameters")
            if isinstance(parameters, dict):
                value: object = parameters.get("retry_after")
                if isinstance(value, int | float) and not isinstance(value, bool):
                    retry_after = float(value)
        status: int = response.status_code
        if status == 429:
            raise TelegramError(
                f"Telegram answered 429{description}", retryable=True, retry_after=retry_after
            )
        if status >= 500:
            raise TelegramError(f"Telegram answered {status}{description}", retryable=True)
        if status != 200:
            raise TelegramError(f"Telegram answered {status}{description}", retryable=False)
        if not isinstance(body, dict):
            raise TelegramError("Telegram answered 200 with a body that is not JSON", retryable=False)
        if body.get("ok") is not True:
            raise TelegramError(f"Telegram answered ok=false{description}", retryable=False)
        return body.get("result")

    def send_message(self, chat_id: int, text: str) -> int:
        result: object = self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}},
        )
        message_id: object = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            raise TelegramError("Telegram answered 200 without message_id", retryable=False)
        return message_id

    def get_updates(self, offset: int, timeout: int) -> list[dict[str, object]]:
        result: object = self._call(
            "getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]}
        )
        if not isinstance(result, list):
            raise TelegramError("Telegram answered 200 without a result list", retryable=False)
        return [item for item in result if isinstance(item, dict)]
