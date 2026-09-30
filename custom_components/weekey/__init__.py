"""The Weekey integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import WeekeyClient
from .config_flow import build_cookie_jar
from .const import CONF_PHPSESSID, DOMAIN
from .coordinator import WeekeyConfigEntry, WeekeyCoordinator

PLATFORMS = [Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: WeekeyConfigEntry) -> bool:
    phpessid = entry.data[CONF_PHPSESSID]

    session = async_create_clientsession(hass, cookie_jar=build_cookie_jar(phpessid))
    client = WeekeyClient(session, phpessid)

    coordinator = WeekeyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
