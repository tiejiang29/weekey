"""Weekey API client."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import aiohttp
from yarl import URL

from .const import (
    BASE_URL,
    ELEVATOR_TYPES,
    PATH_INDEX,
    PATH_ONEKEY,
    PATH_UNLOCK_NEW,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# The vendor's ThinkPHP backend server-renders the caller's identity into the SPA
# shell as a JS string literal that must be double-decoded.
_PAGE_DATA_RE = re.compile(r'const data = "((?:[^"\\]|\\.)*)";', re.DOTALL)

AUTH_EVENT = "auth"


class WeekeyAuthExpired(aiohttp.ClientError):
    """Raised when the server no longer accepts the session cookie."""


class WeekeyApiError(aiohttp.ClientError):
    """Raised for any other transport or protocol level failure."""


class WeekeyOpenFailed(Exception):
    """Raised when the platform accepted the request but the door did not open."""

    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, kw_only=True)
class Gate:
    gate_id: str
    name: str
    garden: str
    device_id: str
    gate_type: str
    online: bool

    @property
    def is_elevator(self) -> bool:
        return self.gate_type in ELEVATOR_TYPES


@dataclass(frozen=True, kw_only=True)
class Account:
    user_id: str
    tenant_name: str
    mobile_present: bool


def _reject_message(body: str) -> str | None:
    """Extract the vendor's `info` text from a rejection body, if it is JSON."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if isinstance(payload, dict):
        info = payload.get("info")
        if isinstance(info, str) and info:
            return info
    return None


def _decode_page_data(html: str) -> dict | None:
    match = _PAGE_DATA_RE.search(html)
    if match is None:
        return None
    try:
        payload = json.loads(json.loads(f'"{match.group(1)}"'))
    except (ValueError, TypeError):
        return None
    if payload.get("event") != AUTH_EVENT:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


class WeekeyClient:
    """Single-account client. Owns no persistent state beyond the cookie jar."""

    def __init__(self, session: aiohttp.ClientSession, phpessid: str) -> None:
        self._session = session
        self.phpessid = phpessid

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _url(self, path: str, **params: str) -> URL:
        url = URL(BASE_URL + path)
        return url.with_query(params) if params else url

    async def _get_json(self, url: URL) -> object:
        try:
            async with self._session.get(
                url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                text = await resp.text()
                if resp.status == 401:
                    raise WeekeyAuthExpired(_reject_message(text) or "会话已失效")
                if resp.status != 200:
                    raise WeekeyApiError(f"HTTP {resp.status}")
        except (aiohttp.ClientError, TimeoutError) as err:
            if isinstance(err, WeekeyAuthExpired):
                raise
            if isinstance(err, WeekeyApiError):
                raise
            raise WeekeyApiError(f"request failed: {err}") from err

        try:
            return json.loads(text)
        except ValueError as err:
            # Without the X-Requested-With marker the vendor answers 200 with a
            # ThinkPHP "跳转提示" HTML page instead of JSON.
            raise WeekeyApiError("non-JSON response") from err

    async def probe_account(self) -> Account | None:
        """Return the account behind the current cookie, or None if not authenticated.

        This is the vendor's own liveness test: the SPA only proceeds when the
        injected payload carries a non-empty `mobile`.
        """
        try:
            async with self._session.get(
                self._url(PATH_INDEX),
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                html = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WeekeyApiError(f"index request failed: {err}") from err

        data = _decode_page_data(html)
        if data is None:
            # Shell served without an injected identity: either a capture/parsing
            # change on the vendor side, or the session is gone.
            _LOGGER.debug("No auth payload in index response (%d bytes)", len(html))
            return None
        if not data.get("mobile"):
            return None

        mp = data.get("mp") or {}
        return Account(
            user_id=str(data.get("user_id") or ""),
            tenant_name=str(mp.get("public_name") or ""),
            mobile_present=True,
        )

    async def fetch_gates(self) -> dict[str, Gate]:
        payload = await self._get_json(self._url(PATH_ONEKEY, new="1"))
        if not isinstance(payload, dict):
            raise WeekeyApiError("unexpected onekey shape")

        if payload.get("status") != 1:
            # Observed expired-session body is `{"status":0,"info":"登录失败呀！"}`
            # carried on HTTP 401, already handled in _get_json. Anything else
            # non-success stays a generic failure.
            raise WeekeyApiError(
                f"onekey returned status={payload.get('status')} info={payload.get('info')}"
            )

        gates: dict[str, Gate] = {}
        for garden in payload.get("info") or []:
            garden_name = str(garden.get("name") or "")
            for dev in garden.get("device") or []:
                gate_id = str(dev.get("id") or "")
                if not gate_id:
                    continue
                gates[gate_id] = Gate(
                    gate_id=gate_id,
                    name=str(dev.get("device_desc") or gate_id),
                    garden=garden_name,
                    device_id=str(dev.get("device_id") or ""),
                    gate_type=str(dev.get("type")),
                    online=bool(dev.get("online")),
                )
        return gates

    async def open_gate(self, gate_id: str, floor: int = 0) -> None:
        """Open one gate exactly once. Never call this again on a timeout."""
        url = self._url(PATH_UNLOCK_NEW, floor=str(floor))
        # Decide from the captured status/text rather than raising inside the try:
        # WeekeyAuthExpired is an aiohttp.ClientError and would be swallowed by
        # the transport except below, turning "not logged in" into "outcome unknown".
        status: int | None = None
        text: str = ""
        try:
            async with self._session.post(
                url,
                headers=self._headers(),
                data={"gate_id": gate_id},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WeekeyOpenFailed(
                "开门结果未知（请求未完成），不要重试", outcome_unknown=True
            ) from err

        if status == 401:
            # Verified: an expired session is rejected before the command is
            # dispatched, so the door did not open.
            raise WeekeyAuthExpired(_reject_message(text) or "会话已失效")
        if status != 200:
            # A 5xx here can mean the command already reached the device.
            raise WeekeyOpenFailed(f"开门结果未知（HTTP {status}），不要重试", outcome_unknown=True)

        try:
            payload = json.loads(text)
        except ValueError as err:
            raise WeekeyOpenFailed("开门结果未知（响应异常）", outcome_unknown=True) from err

        if not isinstance(payload, dict):
            raise WeekeyOpenFailed("开门结果未知（响应异常）", outcome_unknown=True)

        if payload.get("status") == 0 and payload.get("code") is None:
            raise WeekeyAuthExpired(str(payload.get("info") or "会话已失效"))
        if payload.get("code") != 1:
            raise WeekeyApiError(str(payload.get("info") or "平台返回错误"))

        data = payload.get("data") or {}
        # Outer code==1 only means the platform accepted the request. The door
        # itself reports through the nested open.err_code.
        open_info = data.get("open") or {}
        if open_info.get("err_code") != 0:
            raise WeekeyOpenFailed(
                str(open_info.get("err_msg") or open_info.get("msg") or "开门失败")
            )
        _LOGGER.debug("Gate %s opened: %s", gate_id, open_info.get("msg"))
