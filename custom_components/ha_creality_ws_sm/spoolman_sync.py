"""Spoolman integration: keeps input_select options in sync and calls Klipper on active slot change."""
from __future__ import annotations
import logging
import re
import aiohttp

from .const import (  # type: ignore[import]
    CONF_KLIPPER_PORT, DEFAULT_KLIPPER_PORT,
    CONF_SPOOLMAN_PREFIX, DEFAULT_SPOOLMAN_PREFIX,
    CONF_INPUT_SELECT_PREFIX, DEFAULT_INPUT_SELECT_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


class SpoolmanSync:
    """Manages Spoolman↔CFS slot synchronisation for a single printer."""

    def __init__(self, hass, coordinator, config: dict) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._klipper_port: int = int(config.get(CONF_KLIPPER_PORT, DEFAULT_KLIPPER_PORT))
        self._spoolman_prefix: str = config.get(CONF_SPOOLMAN_PREFIX, DEFAULT_SPOOLMAN_PREFIX)
        self._input_select_prefix: str = config.get(CONF_INPUT_SELECT_PREFIX, DEFAULT_INPUT_SELECT_PREFIX)
        self._last_active_slot: int | None = None
        self._unsub_listeners: list = []

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Register HA listeners and schedule the first options sync."""
        pass  # implemented in Task 4

    async def async_unload(self) -> None:
        """Cancel all listeners and scheduled tasks."""
        for unsub in self._unsub_listeners:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
        self._unsub_listeners.clear()

    # ------------------------------------------------------------------
    # Slot index helper
    # ------------------------------------------------------------------

    @staticmethod
    def _entity_to_slot_index(entity_id: str) -> int | None:
        """Map sensor.*_cfs_box_{B}_slot_{S}_filament to a 1-based global slot index.

        Box IDs from WS data start at 1. Formula: (box_id - 1) * 4 + slot_id + 1.
        Returns None if the entity_id does not match the expected pattern.
        """
        m = re.search(r"_cfs_box_(\d+)_slot_(\d+)_filament$", entity_id)
        if not m:
            return None
        box_id = int(m.group(1))
        slot_id = int(m.group(2))
        return (box_id - 1) * 4 + slot_id + 1

    @staticmethod
    def _build_spool_options(states, prefix: str) -> list[str]:
        """Build the options list for input_select entities from Spoolman sensor states.

        Args:
            states: Iterable of state objects with .entity_id and .attributes["friendly_name"].
            prefix: Entity ID prefix for Spoolman spools (e.g. "sensor.spoolman_spool_").

        Returns:
            List starting with "0: Niet in Spoolman" followed by "ID: Name" entries sorted by ID.
        """
        spools: list[tuple[int, str]] = []
        for state in states:
            eid = state.entity_id
            if not eid.startswith(prefix):
                continue
            suffix = eid[len(prefix):]
            if not suffix.isdigit():
                continue
            spool_id = int(suffix)
            raw_name = state.attributes.get("friendly_name", f"Spool {spool_id}")
            # Strip "Spoolman Spool N " prefix from friendly_name if present
            clean_name = raw_name
            for strip_prefix in (f"Spoolman Spool {spool_id} ", f"Spoolman Spool {spool_id}"):
                if raw_name.startswith(strip_prefix):
                    clean_name = raw_name[len(strip_prefix):].strip()
                    break
            spools.append((spool_id, clean_name))

        spools.sort(key=lambda x: x[0])
        return ["0: Niet in Spoolman"] + [f"{sid}: {name}" for sid, name in spools]
