"""Diagnostics support for Weekey."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_PHPSESSID
from .coordinator import WeekeyConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WeekeyConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with every credential and every piece of PII removed.

    HA users paste diagnostics straight into issue trackers, so the session
    cookie, the bound mobile number and the real name must never appear here.
    """
    coordinator = entry.runtime_data
    gates = list(coordinator.data.values())

    return {
        "config_entry_data": {
            # Enough to correlate a cookie without handing it over.
            CONF_PHPSESSID: f"<{len(entry.data[CONF_PHPSESSID])} chars, {entry.data[CONF_PHPSESSID][:4]}…>",
        },
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "update_interval_minutes": coordinator.update_interval.total_seconds() / 60
        if coordinator.update_interval
        else None,
        "gates": [
            {
                "gate_id": gate.gate_id,
                "garden": gate.garden,
                "name": gate.name,
                "type": gate.gate_type,
                "online": gate.online,
                "is_elevator": gate.is_elevator,
                "selected": gate.gate_id in coordinator.selected_gates,
            }
            for gate in gates
        ],
    }
