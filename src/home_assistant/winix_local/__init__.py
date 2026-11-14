"""Home Assistant entry points for Winix Local.

Import HA's runtime only when HA calls an entry point. The provisioning tools
can then import the shared configuration, PKI and codecs normally, without HA.
"""

from ._core.installation import (
    load_bundle,
)  # Preserve the existing bundle-loader import.


async def async_setup_entry(hass, entry):
    from .runtime import async_setup_entry as setup_entry

    return await setup_entry(hass, entry)


async def async_unload_entry(hass, entry):
    from .runtime import async_unload_entry as unload_entry

    return await unload_entry(hass, entry)
