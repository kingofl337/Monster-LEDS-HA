"""Effect dropdown for each light, so effects can be picked from the device page or a dashboard."""
from __future__ import annotations

from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import EFFECT_NONE, LightDevice
from .const import DOMAIN

SOLID_COLOR = "Solid color"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SurplifeEffectSelect(entry.runtime_data)])


class SurplifeEffectSelect(SelectEntity):
    """Mirrors the light's effect; choosing an option turns the light on with it."""

    _attr_has_entity_name = True
    _attr_name = "Effect"
    _attr_icon = "mdi:string-lights"
    _attr_should_poll = False

    def __init__(self, device: LightDevice) -> None:
        self._device = device
        self._attr_unique_id = f"{dr.format_mac(device.address)}_effect"
        self._attr_options = [SOLID_COLOR, *device.effect_list]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device.address)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._device.register_callback(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def current_option(self) -> str:
        return self._device.effect or SOLID_COLOR

    async def async_select_option(self, option: str) -> None:
        try:
            await self._device.turn_on(effect=EFFECT_NONE if option == SOLID_COLOR else option)
        except BLEAK_RETRY_EXCEPTIONS as err:
            raise HomeAssistantError(f"Could not reach {self._device.name}: {err}") from err
