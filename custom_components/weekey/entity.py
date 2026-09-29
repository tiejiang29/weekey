"""Base entity for Weekey."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WeekeyCoordinator


class WeekeyEntity(CoordinatorEntity[WeekeyCoordinator]):
    """Shares the gate list and surfaces per-gate online state as availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WeekeyCoordinator, gate_id: str) -> None:
        super().__init__(coordinator)
        self._gate_id = gate_id
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{gate_id}"
        gate = coordinator.data.get(gate_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, gate_id)},
            name=gate.name if gate else gate_id,
            suggested_area=gate.garden if gate else None,
        )

    @property
    def gate(self):
        return self.coordinator.data.get(self._gate_id)

    @property
    def available(self) -> bool:
        gate = self.gate
        return super().available and gate is not None and gate.online
