"""Spoolman integration: keeps input_select options in sync and calls Klipper on active slot change."""
from __future__ import annotations
import logging
import re
from datetime import timedelta
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # type: ignore[import]
from homeassistant.helpers.event import (  # type: ignore[import]
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.core import EVENT_HOMEASSISTANT_STARTED  # type: ignore[import]

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
        # Track filament type+color per slot to detect changes and reset assignment
        self._slot_filament_cache: dict[int, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Register HA listeners and schedule the first options sync."""
        # Sync options once HA has started (entities are available)
        self._unsub_listeners.append(
            self._hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._sync_options,
            )
        )
        # Re-sync every hour
        self._unsub_listeners.append(
            async_track_time_interval(
                self._hass,
                self._sync_options,
                timedelta(hours=1),
            )
        )
        # Watch CFS filament sensor state changes for active slot detection
        sensor_ids = [
            eid for eid in self._hass.states.async_entity_ids("sensor")
            if "_cfs_box_" in eid and "_slot_" in eid and eid.endswith("_filament")
        ]
        if sensor_ids:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self._hass,
                    sensor_ids,
                    self._on_slot_state_changed,
                )
            )
        else:
            _LOGGER.debug("SpoolmanSync: no CFS filament sensor entities found at setup")

        # Watch input_select entities so picking a spool on an already-active slot
        # immediately updates Klipper without waiting for a slot-activation event
        input_select_ids = [
            eid for eid in self._hass.states.async_entity_ids("input_select")
            if eid.startswith(self._input_select_prefix)
        ]
        if input_select_ids:
            self._unsub_listeners.append(
                async_track_state_change_event(
                    self._hass,
                    input_select_ids,
                    self._on_input_select_changed,
                )
            )

    @property
    def _slot_count(self) -> int:
        """Total number of configured CFS slots from coordinator data."""
        try:
            boxes = self._coordinator.data.get("boxsInfo", {}).get("materialBoxs", [])
            count = sum(len(b.get("materials", [])) for b in boxes if b.get("type") == 0)
            return count if count > 0 else 4  # default to 4 if data not yet available
        except Exception:
            return 4

    async def _sync_options(self, _=None) -> None:
        """Populate input_select options from Spoolman sensor entities."""
        all_states = self._hass.states.async_all()
        options = self._build_spool_options(all_states, self._spoolman_prefix)

        if len(options) <= 1:
            _LOGGER.warning(
                "SpoolmanSync: no Spoolman spool entities found matching prefix '%s'",
                self._spoolman_prefix,
            )

        for slot_index in range(1, self._slot_count + 1):
            entity_id = f"{self._input_select_prefix}{slot_index}"
            try:
                await self._hass.services.async_call(
                    "input_select",
                    "set_options",
                    {"entity_id": entity_id, "options": options},
                    blocking=False,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "SpoolmanSync: could not set options for %s: %s", entity_id, exc
                )

    async def _on_slot_state_changed(self, event) -> None:
        """Handle state change on a CFS filament sensor; sync active spool if selected."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        entity_id = event.data.get("entity_id", "")
        slot_index = self._entity_to_slot_index(entity_id)
        if slot_index is None:
            return

        # Detect filament type/color change — reset spool assignment if changed
        current_type = str(new_state.attributes.get("type") or "")
        current_color = str(new_state.attributes.get("color_hex") or "")
        prev = self._slot_filament_cache.get(slot_index)
        self._slot_filament_cache[slot_index] = (current_type, current_color)
        if prev is not None and prev != (current_type, current_color):
            input_entity = f"{self._input_select_prefix}{slot_index}"
            _LOGGER.info(
                "SpoolmanSync: filament changed on slot %s (%s → %s), resetting spool assignment",
                slot_index, prev, (current_type, current_color),
            )
            try:
                await self._hass.services.async_call(
                    "input_select",
                    "select_option",
                    {"entity_id": input_entity, "option": "0: Niet in Spoolman"},
                    blocking=False,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("SpoolmanSync: could not reset slot %s assignment: %s", slot_index, exc)

        selected = new_state.attributes.get("selected")
        if selected not in (1, True, "1"):
            # Slot became inactive — clear debounce so re-activation fires correctly
            self._last_active_slot = None
            return

        if slot_index == self._last_active_slot:
            return  # debounce — no change
        self._last_active_slot = slot_index

        input_entity = f"{self._input_select_prefix}{slot_index}"
        input_state = self._hass.states.get(input_entity)
        if not input_state:
            _LOGGER.warning(
                "SpoolmanSync: input_select '%s' not found — create it via HA Helpers",
                input_entity,
            )
            return

        selection = input_state.state  # e.g. "3: PETG White" or "0: Niet in Spoolman"
        try:
            spool_id = int(selection.split(":")[0].strip()) if ":" in selection else 0
        except (ValueError, AttributeError):
            spool_id = 0

        if spool_id > 0:
            await self._set_active_spool(spool_id)
        else:
            await self._clear_active_spool()

    async def _on_input_select_changed(self, event) -> None:
        """Call Klipper immediately when a spool is picked on an already-active slot."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        entity_id = event.data.get("entity_id", "")
        # Derive slot index from the input_select entity id
        suffix = entity_id[len(self._input_select_prefix):]
        try:
            slot_index = int(suffix)
        except ValueError:
            return

        # Only act if this slot is currently active
        active_slot_entity = self._find_active_slot_entity(slot_index)
        if not active_slot_entity:
            return

        selection = new_state.state
        try:
            spool_id = int(selection.split(":")[0].strip()) if ":" in selection else 0
        except (ValueError, AttributeError):
            spool_id = 0

        if spool_id > 0:
            await self._set_active_spool(spool_id)
        else:
            await self._clear_active_spool()

    def _find_active_slot_entity(self, slot_index: int) -> str | None:
        """Return the entity_id of the CFS filament sensor for slot_index if it is active."""
        for eid in self._hass.states.async_entity_ids("sensor"):
            if not ("_cfs_box_" in eid and "_slot_" in eid and eid.endswith("_filament")):
                continue
            if self._entity_to_slot_index(eid) != slot_index:
                continue
            st = self._hass.states.get(eid)
            if st and st.attributes.get("selected") in (1, True, "1"):
                return eid
        return None

    async def _set_active_spool(self, spool_id: int) -> None:
        """Call Klipper to set the active spool."""
        host = self._coordinator.client._host
        url = f"http://{host}:{self._klipper_port}/printer/gcode/script"
        params = {"script": f"SET_ACTIVE_SPOOL ID={spool_id}"}
        await self._post_klipper(url, params)
        _LOGGER.info("SpoolmanSync: SET_ACTIVE_SPOOL ID=%s", spool_id)

    async def _clear_active_spool(self) -> None:
        """Call Klipper to clear the active spool."""
        host = self._coordinator.client._host
        url = f"http://{host}:{self._klipper_port}/printer/gcode/script"
        params = {"script": "CLEAR_ACTIVE_SPOOL"}
        await self._post_klipper(url, params)
        _LOGGER.info("SpoolmanSync: CLEAR_ACTIVE_SPOOL")

    async def _post_klipper(self, url: str, params: dict) -> None:
        """POST a GCode script to the Klipper REST endpoint."""
        session = async_get_clientsession(self._hass)
        try:
            async with session.post(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "SpoolmanSync: Klipper returned HTTP %s for %s",
                        resp.status,
                        url,
                    )
        except aiohttp.ClientError as exc:
            _LOGGER.error("SpoolmanSync: Failed to call Klipper at %s: %s", url, exc)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("SpoolmanSync: Unexpected error calling Klipper: %s", exc)

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
