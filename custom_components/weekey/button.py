"""Button platform for Weekey: one press opens one gate."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import WeekeyApiError, WeekeyAuthExpired, WeekeyOpenFailed
from .const import CONF_GATE_IDS, UNLOCK_COOLDOWN
from .coordinator import WeekeyConfigEntry, WeekeyCoordinator
from .entity import WeekeyEntity

# Presses are serialised per gate by the entity's own lock.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeekeyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        WeekeyGateButton(coordinator, gate_id)
        for gate_id in entry.options.get(CONF_GATE_IDS, [])
    )


class WeekeyGateButton(WeekeyEntity, ButtonEntity):
    """A door release. Stateless by design: the platform never reports lock state."""

    _attr_translation_key = "open"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator: WeekeyCoordinator, gate_id: str) -> None:
        super().__init__(coordinator, gate_id)
        self._lock = asyncio.Lock()
        self._last_open: float | None = None

    async def async_press(self) -> None:
        if self._lock.locked():
            raise HomeAssistantError("该门正在开门中，请等待")

        async with self._lock:
            if self._last_open is not None:
                remaining = UNLOCK_COOLDOWN - (monotonic() - self._last_open)
                if remaining > 0:
                    raise HomeAssistantError(
                        f"开门过于频繁，请 {int(remaining) + 1} 秒后再试"
                    )

            try:
                await self.coordinator.client.open_gate(self._gate_id)
            except WeekeyAuthExpired as err:
                self.coordinator.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("Weekey 会话已失效，请重新粘贴 PHPSESSID") from err
            except WeekeyOpenFailed as err:
                # outcome_unknown stays in the log: telling the user "结果未知"
                # is correct here, and retrying from the UI would be wrong.
                _LOGGER.warning("Gate %s: %s", self._gate_id, err)
                raise HomeAssistantError(str(err)) from err
            except WeekeyApiError as err:
                raise HomeAssistantError(f"开门请求失败：{err}") from err

            # Recorded only on a confirmed success, so a failed attempt does not
            # lock out the user who is standing at the door.
            self._last_open = monotonic()
