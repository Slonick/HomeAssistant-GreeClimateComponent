"""Support for Gree select entities (e.g., external temperature sensor selection)."""

from __future__ import annotations

# Standard library imports
import logging
from collections.abc import Callable
from dataclasses import dataclass

# Home Assistant imports
from homeassistant.components.climate import HVACMode
from homeassistant.components.select import (
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

# Local imports
from .const import DEFAULT_SMART_WIND_MODES, DOMAIN
from .entity import GreeEntity, GreeEntityDescription

_LOGGER = logging.getLogger(__name__)


@dataclass
class GreeSelectEntityDescription(GreeEntityDescription, SelectEntityDescription):
    """Describes Gree select entity."""

    set_fn: Callable[[object, str], None] = None
    restore_state: bool = False
    options_fn: Callable[[object], list[str]] = None


def get_temperature_sensor_options(hass: HomeAssistant) -> list[str]:
    """Get list of available temperature sensor entities."""
    options = ["None"]  # Always include "None" as first option

    # Get all entities from the registry
    for state in hass.states.async_all():
        # Look for temperature sensors
        if state.entity_id.startswith("sensor."):
            # Check for explicit device_class
            if state.attributes.get("device_class") == "temperature":
                options.append(state.entity_id)
            # Also check for temperature units as fallback for helpers/combined sensors
            elif state.attributes.get("unit_of_measurement") in ["°C", "°F", "K"]:
                options.append(state.entity_id)

    return options


async def _set_external_temperature_sensor(device, value: str) -> None:
    device._external_temperature_sensor = None if value == "None" else value


async def _set_smart_wind(device, value: str) -> None:
    await device.async_set_smart_wind_mode(value)


SELECTS: tuple[GreeSelectEntityDescription, ...] = (
    GreeSelectEntityDescription(
        property_key="external_temperature_sensor",
        icon="mdi:thermometer-lines",
        options=[],  # Will be populated dynamically
        value_fn=lambda device: getattr(device, "_external_temperature_sensor", None) or "None",
        set_fn=_set_external_temperature_sensor,
        entity_category=EntityCategory.CONFIG,
        restore_state=True,
        options_fn=lambda hass: get_temperature_sensor_options(hass),
    ),
    GreeSelectEntityDescription(
        property_key="smart_wind",
        icon="mdi:air-conditioner",
        options=DEFAULT_SMART_WIND_MODES,
        value_fn=lambda device: device.smart_wind_mode,
        set_fn=_set_smart_wind,
        exists_fn=lambda description, device: getattr(device, "_has_smart_wind", None) is not False,
        # The body sensing device only steers the louvers while heating or cooling.
        available_fn=lambda device: (
            device.available
            and getattr(device, "_has_smart_wind", False)
            and device._hvac_mode in (HVACMode.COOL, HVACMode.HEAT)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gree select entities based on a config entry."""
    device = hass.data[DOMAIN][entry.entry_id]["device"]
    async_add_entities(
        GreeSelectEntity(hass, entry, description)
        for description in SELECTS
        if description.exists_fn(description, device)
    )


class GreeSelectEntity(GreeEntity, SelectEntity, RestoreEntity):
    """Defines a Gree select entity."""

    entity_description: GreeSelectEntityDescription

    def __init__(self, hass: HomeAssistant, entry, description: GreeSelectEntityDescription) -> None:
        super().__init__(hass, entry, description)
        self._hass = hass
        # Set up options dynamically
        if description.options_fn:
            self._attr_options = description.options_fn(hass)
        else:
            self._attr_options = list(description.options or ["None"])

    async def async_added_to_hass(self) -> None:
        """Restore state when entity is added to hass."""
        await super().async_added_to_hass()

        # Refresh options when entity is added
        if self.entity_description.options_fn:
            self._attr_options = self.entity_description.options_fn(self._hass)

        # Restore the last selected state if available
        if self.entity_description.restore_state:
            restored = await self.async_get_last_state()
            if restored and self.entity_description.set_fn:
                await self.entity_description.set_fn(self._device, restored.state)
                _LOGGER.debug("Restored %s state: %s", self.entity_id, restored.state)

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(self._device)
        return None

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        if option not in self._attr_options:
            _LOGGER.error("Option %s not available in %s", option, self._attr_options)
            return

        if self.entity_description.set_fn:
            await self.entity_description.set_fn(self._device, option)
            self.async_write_ha_state()
            _LOGGER.info("Selected %s: %s", self.entity_description.property_key, option)

    async def async_update(self) -> None:
        """Update the entity."""
        # Refresh available temperature sensors periodically
        if self.entity_description.options_fn:
            new_options = self.entity_description.options_fn(self._hass)
            if new_options != self._attr_options:
                self._attr_options = new_options
                _LOGGER.debug("Updated temperature sensor options: %s", self._attr_options)
