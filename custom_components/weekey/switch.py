"""Switch platform for Weekey: the switch turns itself back off."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from time import monotonic

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .client import WeekeyApiError, WeekeyAuthExpired, WeekeyOpenFailed
from .const import CONF_GATE_IDS, SWITCH_RESET_SECONDS, UNLOCK_COOLDOWN
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
        WeekeyGateSwitch(coordinator, gate_id)
        for gate_id in entry.options.get(CONF_GATE_IDS, [])
    )


class WeekeyGateSwitch(WeekeyEntity, SwitchEntity):
    """A momentary door release presented as a switch.

    ``on`` means "a release command was just accepted by the platform", NOT "the
    door is open" — the vendor exposes no lock state at all. The entity schedules
    its own fall-back edge so the UI never sticks on.
    """

    _attr_translation_key = "open"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator: WeekeyCoordinator, gate_id: str) -> None:
        super().__init__(coordinator, gate_id)
        self._lock = asyncio.Lock()
        self._last_open: float | None = None
        self._cancel_reset: Callable[[], None] | None = None
        self._attr_is_on = False

    def _schedule_reset(self) -> None:
        self._unschedule_reset()
        self._cancel_reset = async_call_later(
            self.hass, SWITCH_RESET_SECONDS, self._async_reset
        )

    def _unschedule_reset(self) -> None:
        if self._cancel_reset is not None:
            self._cancel_reset()
            self._cancel_reset = None

    # @callback is load-bearing: async_call_later wraps this in a HassJob, and a
    # bare function resolves to HassJobType.Executor, which would run
    # async_write_ha_state() off the event loop.
    @callback
    def _async_reset(self, _now: datetime) -> None:
        self._cancel_reset = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: object) -> None:
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
                self._attr_is_on = False
                self.coordinator.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError("Weekey 会话已失效，请重新粘贴 PHPSESSID") from err
            except WeekeyOpenFailed as err:
                # outcome_unknown: the command may still have reached the device,
                # so leave the switch off and let the user look at the door.
                self._attr_is_on = False
                _LOGGER.warning("Gate %s: %s", self._gate_id, err)
                raise HomeAssistantError(str(err)) from err
            except WeekeyApiError as err:
                self._attr_is_on = False
                raise HomeAssistantError(f"开门请求失败：{err}") from err

            # Only a platform-confirmed accept lights the switch, and the clock
            # for the cooldown starts here too.
            self._last_open = monotonic()
            self._attr_is_on = True
            self._schedule_reset()

    async def async_turn_off(self, **kwargs: object) -> None:
        """Clear the synthetic state. Deliberately performs no physical action."""
        self._unschedule_reset()
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        self._unschedule_reset()
        await super().async_will_remove_from_hass()
