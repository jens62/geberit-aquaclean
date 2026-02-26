"""Geberit AquaClean integration."""
from __future__ import annotations

import logging
import pathlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# aquaclean_console_app uses custom log levels TRACE (DEBUG-5) and SILLY (DEBUG-7)
# that are registered at module level in main.py — which is never imported in HAOS.
# Add them to logging.Logger directly so HassLogger (a subclass) inherits them.
def _register_custom_log_levels() -> None:
    _TRACE = logging.DEBUG - 5
    _SILLY = logging.DEBUG - 7

    if not hasattr(logging.Logger, "trace"):
        logging.addLevelName(_TRACE, "TRACE")
        logging.TRACE = _TRACE  # type: ignore[attr-defined]

        def _trace(self: logging.Logger, msg: object, *args: object, **kwargs: object) -> None:
            if self.isEnabledFor(_TRACE):
                self._log(_TRACE, msg, args, **kwargs)

        logging.Logger.trace = _trace  # type: ignore[attr-defined]

    if not hasattr(logging.Logger, "silly"):
        logging.addLevelName(_SILLY, "SILLY")
        logging.SILLY = _SILLY  # type: ignore[attr-defined]

        def _silly(self: logging.Logger, msg: object, *args: object, **kwargs: object) -> None:
            if self.isEnabledFor(_SILLY):
                self._log(_SILLY, msg, args, **kwargs)

        logging.Logger.silly = _silly  # type: ignore[attr-defined]

_register_custom_log_levels()

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator

PLATFORMS = ["binary_sensor", "sensor", "button"]

_ICONS_JS = pathlib.Path(__file__).parent / "www" / "geberit-aquaclean-icons.js"
_ICONS_URL = "/geberit_aquaclean/geberit-aquaclean-icons.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the custom icon set as a Lovelace frontend resource."""
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.frontend import add_extra_js_url

    await hass.http.async_register_static_paths(
        [StaticPathConfig(_ICONS_URL, str(_ICONS_JS), cache_headers=True)]
    )
    add_extra_js_url(hass, _ICONS_URL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = AquaCleanCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reload the entry when the user saves new options — picks up changed settings.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
