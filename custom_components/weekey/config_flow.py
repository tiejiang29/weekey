"""Config flow for Weekey."""

from __future__ import annotations

import http.cookies
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from yarl import URL

from .client import Account, WeekeyApiError, WeekeyAuthExpired, WeekeyClient
from .const import (
    BASE_URL,
    CONF_GATE_IDS,
    CONF_PHPSESSID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    MIN_UPDATE_INTERVAL_MIN,
)
from .coordinator import WeekeyConfigEntry

_LOGGER = logging.getLogger(__name__)


class _CannotConnect(HomeAssistantError):
    """Transport failure during a flow; distinct from "cookie not accepted"."""


def build_cookie_jar(phpessid: str) -> aiohttp.CookieJar:
    """Seed a jar with the session cookie so the LB cookie rotates underneath it."""
    jar = aiohttp.CookieJar()
    cookie = http.cookies.SimpleCookie()
    cookie["PHPSESSID"] = phpessid
    cookie["PHPSESSID"]["path"] = "/"
    jar.update_cookies(cookie, URL(BASE_URL))
    return jar


def _normalize_phpessid(raw: str) -> str:
    value = raw.strip()
    if value.lower().startswith("phpsessid="):
        value = value.split("=", 1)[1]
    return value.strip().strip('"').strip("'")


async def _validate(phpsessid: str) -> tuple[Account | None, list[str]]:
    """One session, two calls: identity from the SPA shell, then the gate list.

    Returns (None, []) when the cookie is not accepted. The gate list is fetched
    here so the entry is created already populated — an entry with no selection
    would silently produce zero entities.
    """
    session = aiohttp.ClientSession(cookie_jar=build_cookie_jar(phpsessid))
    try:
        client = WeekeyClient(session, phpsessid)
        account = await client.probe_account()
        if account is None or not account.user_id:
            return None, []
        gate_ids: list[str] = []
        try:
            gates = await client.fetch_gates()
        except (WeekeyApiError, WeekeyAuthExpired):
            # Session verified but the list call failed (transient, or the cookie
            # rotated between calls). Create the entry anyway; the coordinator
            # will surface a persistent failure.
            _LOGGER.debug("Gate list unavailable during setup; creating with no selection")
        else:
            gate_ids = [g.gate_id for g in gates.values() if not g.is_elevator]
    except WeekeyApiError as err:
        raise _CannotConnect(str(err)) from err
    finally:
        await session.close()
    return account, gate_ids


class WeekeyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user and reauth flows. Reauth reuses the same single field."""

    VERSION = 1

    def _schema(self, phpsessid: str = "") -> vol.Schema:
        return vol.Schema({vol.Required(CONF_PHPSESSID, default=phpsessid): str})

    async def _async_step_credentials(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        # For reauth, show the currently stored value as the default. Never treat
        # it as submitted input, or the form would validate on first render.
        defaults: dict[str, Any] = {}
        if step_id == "reauth_confirm":
            defaults = self._get_reauth_entry().data

        if user_input is not None:
            phpsessid = _normalize_phpessid(user_input[CONF_PHPSESSID])
            try:
                account, gate_ids = await _validate(phpsessid)
            except _CannotConnect:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=self._schema(phpsessid),
                    errors={"base": "cannot_connect"},
                )
            if account is None:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=self._schema(phpsessid),
                    errors={CONF_PHPSESSID: "invalid_session"},
                )

            if self.source == SOURCE_REAUTH:
                # Options are untouched here on purpose: a re-auth must not reset
                # which gates the user already chose.
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_PHPSESSID: phpsessid},
                )

            await self.async_set_unique_id(account.user_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=account.tenant_name or "Weekey",
                data={CONF_PHPSESSID: phpsessid},
                options={
                    CONF_GATE_IDS: gate_ids,
                    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL_MIN,
                },
            )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema(defaults.get(CONF_PHPSESSID, "")),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_step_credentials("user", user_input)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_step_credentials("reauth_confirm", user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: WeekeyConfigEntry,
    ) -> WeekeyOptionsFlow:
        return WeekeyOptionsFlow(config_entry)


class WeekeyOptionsFlow(OptionsFlowWithReload):
    """Pick which gates become buttons, and how often to refresh the list."""

    def __init__(self, config_entry: WeekeyConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # A dead session still needs an editable gate list, so runtime_data may
        # be absent when the user opens options after a failed setup.
        coordinator = getattr(self._entry, "runtime_data", None)
        options = [
            {"value": gate.gate_id, "label": f"{gate.garden} · {gate.name}"}
            for gate in sorted(coordinator.data.values(), key=lambda g: g.name)
            if not gate.is_elevator
        ] if coordinator else []

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_GATE_IDS,
                        default=list(self._entry.options.get(CONF_GATE_IDS, [])),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                            sort=True,
                        )
                    ),
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=self._entry.options.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MIN
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_UPDATE_INTERVAL_MIN,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="minutes",
                        )
                    ),
                }
            ),
        )
