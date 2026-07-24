"""Support for Gree sensors."""

from __future__ import annotations

# Standard library imports
import logging
from dataclasses import dataclass

# Home Assistant imports
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)

# Local imports
from .const import DOMAIN
from .entity import GreeEntity, GreeEntityDescription

_LOGGER = logging.getLogger(__name__)


@dataclass
class GreeSensorEntityDescription(GreeEntityDescription, SensorEntityDescription):
    """Describes Gree Sensor entity."""


SENSORS: tuple[GreeSensorEntityDescription, ...] = (
    # The climate entity reports whichever sensor drives it, which is the external one when
    # configured. This exposes what the unit itself reads, so both stay available.
    GreeSensorEntityDescription(
        property_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device._builtin_temperature,
        exists_fn=lambda description, device: device._has_temp_sensor is not False,
        available_fn=lambda device: device.available and device._builtin_temperature is not None,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Gree sensors from a config entry."""
    device = hass.data[DOMAIN][entry.entry_id]["device"]
    async_add_entities(
        GreeSensor(hass, entry, description)
        for description in SENSORS
        if description.exists_fn(description, device)
    )


class GreeSensor(GreeEntity, SensorEntity):
    """Gree sensor entity."""

    entity_description: GreeSensorEntityDescription

    def __init__(self, hass, entry, description: GreeSensorEntityDescription) -> None:
        """Initialize Gree sensor."""
        super().__init__(hass, entry, description)

        if description.device_class == SensorDeviceClass.TEMPERATURE:
            self._attr_native_unit_of_measurement = self._device.temperature_unit

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.entity_description.value_fn(self._device)
