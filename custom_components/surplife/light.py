"""Light entity for Surplife Bluetooth string lights."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import color as color_util

from .base import LightDevice
from .const import DOMAIN

SERVICE_SET_LEDS = "set_leds"
ATTR_LEDS = "leds"
ATTR_RGB = "rgb_color"
ATTR_BRIGHTNESS_PCT = "brightness_pct"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([SurplifeLight(entry.runtime_data, entry)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_LEDS,
        {
            vol.Required(ATTR_LEDS): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1))]),
            vol.Required(ATTR_RGB): vol.All(vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple)),
            vol.Optional(ATTR_BRIGHTNESS_PCT, default=100): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        },
        "async_set_leds",
    )


class SurplifeLight(LightEntity):
    """The whole light string as one entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_color_mode = ColorMode.HS
    _attr_supported_features = LightEntityFeature.EFFECT

    def __init__(self, device: LightDevice, entry: ConfigEntry) -> None:
        self._device = device
        self._attr_effect_list = [EFFECT_OFF, *device.effect_list]
        self._attr_assumed_state = device.assumed_state
        self._attr_unique_id = dr.format_mac(device.address)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
            name=entry.title,
            manufacturer="Surplife",
            model="Surplife Bluetooth string lights",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._device.register_callback(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def is_on(self) -> bool | None:
        return self._device.is_on

    @property
    def brightness(self) -> int:
        return max(1, round(self._device.brightness_pct * 255 / 100))

    @property
    def hs_color(self) -> tuple[float, float]:
        return self._device.hs

    @property
    def effect(self) -> str:
        return self._device.effect or EFFECT_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"led_count": self._device.pixel_count}

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        pct = None if brightness is None else max(1, round(brightness * 100 / 255))
        try:
            await self._device.turn_on(
                hs=kwargs.get(ATTR_HS_COLOR), brightness_pct=pct, effect=kwargs.get(ATTR_EFFECT)
            )
        except BLEAK_RETRY_EXCEPTIONS as err:
            raise HomeAssistantError(f"Could not reach {self._device.name}: {err}") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._device.turn_off()
        except BLEAK_RETRY_EXCEPTIONS as err:
            raise HomeAssistantError(f"Could not reach {self._device.name}: {err}") from err

    async def async_set_leds(
        self, leds: list[int], rgb_color: tuple[int, int, int], brightness_pct: float = 100
    ) -> None:
        hs = color_util.color_RGB_to_hs(*rgb_color)
        try:
            await self._device.set_leds(leds, hs, brightness_pct)
        except BLEAK_RETRY_EXCEPTIONS as err:
            raise HomeAssistantError(f"Could not reach {self._device.name}: {err}") from err
