"""Data update coordinator for Weekey."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import Gate, WeekeyApiError, WeekeyAuthExpired, WeekeyClient
from .const import CONF_GATE_IDS, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MIN

_LOGGER = logging.getLogger(__name__)

type WeekeyConfigEntry = ConfigEntry[WeekeyCoordinator]


class WeekeyCoordinator(DataUpdateCoordinator[dict[str, Gate]]):
    """Polls `onekey`, which doubles as the session liveness probe."""

    config_entry: WeekeyConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WeekeyConfigEntry, client: WeekeyClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Weekey",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MIN)
            ),
        )
        self.client = client
        self._selected = set(entry.options.get(CONF_GATE_IDS, []))

    @property
    def selected_gates(self) -> set[str]:
        return self._selected

    async def _async_update_data(self) -> dict[str, Gate]:
        try:
            gates = await self.client.fetch_gates()
        except WeekeyAuthExpired as err:
            raise ConfigEntryAuthFailed("Weekey 会话已失效，需要重新登录") from err
        except WeekeyApiError as err:
            raise UpdateFailed(f"Weekey 请求失败: {err}") from err

        for gate in gates.values():
            if gate.is_elevator and gate.gate_id in self._selected:
                _LOGGER.warning(
                    "Gate %s (%s) is an elevator call target and will not be exposed as a door button",
                    gate.gate_id,
                    gate.name,
                )
        return gates
