# Spoolman Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native Spoolman spool assignment to the CFS card, with a type-filtered dropdown per slot and automatic Klipper sync when the active slot changes.

**Architecture:** A new `SpoolmanSync` Python module attaches to the coordinator and handles two jobs: keeping `input_select.cfs_slot_*` options populated from Spoolman sensor entities, and watching for active-slot changes to call the Klipper REST API. The CFS card JS gains a spool dropdown on each slot card that reads Spoolman entities from `hass.states` and writes selections to the corresponding `input_select`.

**Tech Stack:** Python (asyncio, aiohttp, HA helpers), vanilla JS (Web Components, hass API), pytest (no HA test infra — pure unit tests with stubs matching existing project pattern).

**Spec:** `docs/superpowers/specs/2026-04-05-spoolman-integration-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `custom_components/ha_creality_ws_sm/const.py` | Modify | Add 6 new Spoolman config constants |
| `custom_components/ha_creality_ws_sm/spoolman_sync.py` | Create | SpoolmanSync class — options refresh + active spool watcher + Klipper REST |
| `custom_components/ha_creality_ws_sm/coordinator.py` | Modify | Load Spoolman config in `_load_options()`, instantiate/teardown SpoolmanSync |
| `custom_components/ha_creality_ws_sm/__init__.py` | Modify | Import new constants, pass Spoolman config to coordinator setup |
| `custom_components/ha_creality_ws_sm/config_flow.py` | Modify | Add Spoolman fields to OptionsFlowHandler |
| `custom_components/ha_creality_ws_sm/strings.json` | Modify | Add labels for new options fields |
| `custom_components/ha_creality_ws_sm/translations/en.json` | Modify | Mirror strings.json |
| `custom_components/ha_creality_ws_sm/www/k_cfs_card.js` | Modify | Add spool dropdown to slot cards, type-filtered options |
| `tools/tests/test_spoolman_sync.py` | Create | Unit tests for SpoolmanSync logic |

---

## Task 1: Add constants

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/const.py`

- [ ] **Step 1: Add Spoolman constants to `const.py`**

Append to the end of `custom_components/ha_creality_ws_sm/const.py`:

```python
# Spoolman integration
CONF_SPOOLMAN_ENABLED = "spoolman_enabled"
CONF_KLIPPER_PORT = "klipper_port"
CONF_SPOOLMAN_PREFIX = "spoolman_prefix"
CONF_INPUT_SELECT_PREFIX = "input_select_prefix"
DEFAULT_KLIPPER_PORT = 4408
DEFAULT_SPOOLMAN_PREFIX = "sensor.spoolman_spool_"
DEFAULT_INPUT_SELECT_PREFIX = "input_select.cfs_slot_"
```

- [ ] **Step 2: Commit**

```bash
git add custom_components/ha_creality_ws_sm/const.py
git commit -m "feat: add Spoolman config constants"
```

---

## Task 2: Create `SpoolmanSync` — slot index helper (TDD)

**Files:**
- Create: `custom_components/ha_creality_ws_sm/spoolman_sync.py`
- Create: `tools/tests/test_spoolman_sync.py`

- [ ] **Step 1: Create stub `spoolman_sync.py` with just the class shell and `_entity_to_slot_index`**

```python
"""Spoolman integration: keeps input_select options in sync and calls Klipper on active slot change."""
from __future__ import annotations
import logging
import re
import aiohttp

_LOGGER = logging.getLogger(__name__)


class SpoolmanSync:
    """Manages Spoolman↔CFS slot synchronisation for a single printer."""

    def __init__(self, hass, coordinator, config: dict) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._klipper_port: int = int(config.get("klipper_port", 4408))
        self._spoolman_prefix: str = config.get("spoolman_prefix", "sensor.spoolman_spool_")
        self._input_select_prefix: str = config.get("input_select_prefix", "input_select.cfs_slot_")
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
```

- [ ] **Step 2: Write failing tests for `_entity_to_slot_index`**

Create `tools/tests/test_spoolman_sync.py`:

```python
"""Unit tests for SpoolmanSync."""
import sys
from pathlib import Path
import types

# ---------------------------------------------------------------------------
# Minimal stubs so spoolman_sync.py imports without a real HA environment
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Stub aiohttp so the import doesn't fail
aiohttp_stub = types.ModuleType("aiohttp")
class _Timeout:
    def __init__(self, **kwargs): pass
aiohttp_stub.ClientTimeout = _Timeout
aiohttp_stub.ClientError = Exception
sys.modules.setdefault("aiohttp", aiohttp_stub)

from custom_components.ha_creality_ws_sm.spoolman_sync import SpoolmanSync  # noqa: E402


class TestEntityToSlotIndex:
    def test_box1_slot0_returns_1(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_abc_cfs_box_1_slot_0_filament"
        ) == 1

    def test_box1_slot3_returns_4(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_8fd7_cfs_box_1_slot_3_filament"
        ) == 4

    def test_box2_slot0_returns_5(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_abc_cfs_box_2_slot_0_filament"
        ) == 5

    def test_box2_slot3_returns_8(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_abc_cfs_box_2_slot_3_filament"
        ) == 8

    def test_non_matching_returns_none(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_abc_bed_temperature"
        ) is None

    def test_color_entity_returns_none(self):
        assert SpoolmanSync._entity_to_slot_index(
            "sensor.k2_abc_cfs_box_1_slot_0_color"
        ) is None
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd d:/Development/ha_creality_ws && python -m pytest tools/tests/test_spoolman_sync.py -v
```

Expected: `ImportError` or `AttributeError` — `SpoolmanSync` exists but test file can't import yet (stub path not set up).

Actually expected: **PASS** on these tests since `_entity_to_slot_index` is already implemented in Step 1. Verify all 6 tests pass.

- [ ] **Step 4: Commit**

```bash
git add custom_components/ha_creality_ws_sm/spoolman_sync.py tools/tests/test_spoolman_sync.py
git commit -m "feat: add SpoolmanSync skeleton with slot index helper"
```

---

## Task 3: `SpoolmanSync` — options builder (TDD)

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/spoolman_sync.py`
- Modify: `tools/tests/test_spoolman_sync.py`

- [ ] **Step 1: Write failing tests for `_build_spool_options`**

Append to `tools/tests/test_spoolman_sync.py`:

```python
from types import SimpleNamespace


def _make_state(entity_id: str, friendly_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id,
        attributes={"friendly_name": friendly_name},
    )


class TestBuildSpoolOptions:
    PREFIX = "sensor.spoolman_spool_"

    def _states(self):
        return [
            _make_state("sensor.spoolman_spool_3", "Spoolman Spool 3 PETG White"),
            _make_state("sensor.spoolman_spool_7", "Spoolman Spool 7 PETG Black"),
            _make_state("sensor.spoolman_spool_1", "Spoolman Spool 1 PLA Red"),
            _make_state("sensor.spoolman_spool_12", "Spoolman Spool 12 ABS Grey"),
        ]

    def test_always_starts_with_zero_option(self):
        opts = SpoolmanSync._build_spool_options(self._states(), self.PREFIX)
        assert opts[0] == "0: Niet in Spoolman"

    def test_strips_spoolman_prefix_from_name(self):
        opts = SpoolmanSync._build_spool_options(self._states(), self.PREFIX)
        assert "3: PETG White" in opts
        assert "1: PLA Red" in opts

    def test_sorted_by_id(self):
        opts = SpoolmanSync._build_spool_options(self._states(), self.PREFIX)
        ids = [int(o.split(":")[0]) for o in opts if ":" in o and o.split(":")[0].strip().isdigit()]
        assert ids == sorted(ids)

    def test_ignores_non_numeric_suffix(self):
        states = self._states() + [
            _make_state("sensor.spoolman_spool_settings", "Settings"),
        ]
        opts = SpoolmanSync._build_spool_options(states, self.PREFIX)
        assert all("settings" not in o.lower() for o in opts)

    def test_empty_states_returns_only_zero_option(self):
        opts = SpoolmanSync._build_spool_options([], self.PREFIX)
        assert opts == ["0: Niet in Spoolman"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tools/tests/test_spoolman_sync.py::TestBuildSpoolOptions -v
```

Expected: `AttributeError: type object 'SpoolmanSync' has no attribute '_build_spool_options'`

- [ ] **Step 3: Implement `_build_spool_options` as a static method in `spoolman_sync.py`**

Add after `_entity_to_slot_index`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tools/tests/test_spoolman_sync.py::TestBuildSpoolOptions -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_creality_ws_sm/spoolman_sync.py tools/tests/test_spoolman_sync.py
git commit -m "feat: add SpoolmanSync._build_spool_options with tests"
```

---

## Task 4: `SpoolmanSync` — async core (options sync + active spool watcher)

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/spoolman_sync.py`
- Modify: `tools/tests/test_spoolman_sync.py`

- [ ] **Step 1: Write failing tests for async behaviour**

Append to `tools/tests/test_spoolman_sync.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_hass(spool_states=None, input_select_state=None):
    """Return a minimal hass stub for SpoolmanSync tests."""
    hass = MagicMock()

    # States
    all_states = spool_states or []
    hass.states.async_all.return_value = all_states

    def _get_state(eid):
        if input_select_state and eid == input_select_state[0]:
            return SimpleNamespace(state=input_select_state[1])
        return None

    hass.states.get.side_effect = _get_state

    # Services
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()

    # Bus (events)
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=lambda: None)

    return hass


def _make_coordinator(host="192.168.1.100"):
    coord = MagicMock()
    coord.client._host = host
    return coord


class TestSyncOptions:
    def test_calls_set_options_for_each_slot(self):
        async def run():
            spool_states = [
                _make_state("sensor.spoolman_spool_3", "Spoolman Spool 3 PETG White"),
                _make_state("sensor.spoolman_spool_1", "Spoolman Spool 1 PLA Red"),
            ]
            hass = _make_hass(spool_states=spool_states)
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {
                "spoolman_prefix": "sensor.spoolman_spool_",
                "input_select_prefix": "input_select.cfs_slot_",
                # slot_count comes from coordinator.data; MagicMock triggers except→default 4
            })
            await sync._sync_options()

            assert hass.services.async_call.call_count == 4
            call_args = hass.services.async_call.call_args_list[0]
            domain, service, data = call_args[0]
            assert domain == "input_select"
            assert service == "set_options"
            assert data["options"][0] == "0: Niet in Spoolman"
            assert "3: PETG White" in data["options"]

        asyncio.run(run())

    def test_skips_missing_input_select_with_warning(self):
        async def run():
            hass = _make_hass(spool_states=[])
            hass.services.async_call = AsyncMock(side_effect=Exception("entity not found"))
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {"slot_count": 2})
            # Should not raise even if service call fails
            await sync._sync_options()

        asyncio.run(run())


class TestActiveSlotWatcher:
    def test_set_spool_called_when_slot_becomes_active(self):
        async def run():
            hass = _make_hass(
                input_select_state=("input_select.cfs_slot_4", "3: PETG White")
            )
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {})

            # Simulate a state change event: slot 4 becomes selected
            new_state = SimpleNamespace(
                entity_id="sensor.k2_8fd7_cfs_box_1_slot_3_filament",
                attributes={"selected": 1},
            )
            event = SimpleNamespace(data={
                "entity_id": "sensor.k2_8fd7_cfs_box_1_slot_3_filament",
                "new_state": new_state,
            })

            posted_urls = []
            async def fake_post(url, **kwargs):
                posted_urls.append(url)
                resp = MagicMock()
                resp.status = 200
                resp.__aenter__ = AsyncMock(return_value=resp)
                resp.__aexit__ = AsyncMock(return_value=False)
                return resp

            mock_session = MagicMock()
            mock_session.post = fake_post

            with patch(
                "custom_components.ha_creality_ws_sm.spoolman_sync.async_get_clientsession",
                return_value=mock_session,
            ):
                await sync._on_slot_state_changed(event)

            assert any("SET_ACTIVE_SPOOL" in u and "ID=3" in u for u in posted_urls)

        asyncio.run(run())

    def test_clear_spool_called_when_no_spool_assigned(self):
        async def run():
            hass = _make_hass(
                input_select_state=("input_select.cfs_slot_1", "0: Niet in Spoolman")
            )
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {})

            new_state = SimpleNamespace(
                entity_id="sensor.k2_abc_cfs_box_1_slot_0_filament",
                attributes={"selected": 1},
            )
            event = SimpleNamespace(data={
                "entity_id": "sensor.k2_abc_cfs_box_1_slot_0_filament",
                "new_state": new_state,
            })

            posted_urls = []
            async def fake_post(url, **kwargs):
                posted_urls.append(url)
                resp = MagicMock()
                resp.status = 200
                resp.__aenter__ = AsyncMock(return_value=resp)
                resp.__aexit__ = AsyncMock(return_value=False)
                return resp

            mock_session = MagicMock()
            mock_session.post = fake_post

            with patch(
                "custom_components.ha_creality_ws_sm.spoolman_sync.async_get_clientsession",
                return_value=mock_session,
            ):
                await sync._on_slot_state_changed(event)

            assert any("CLEAR_ACTIVE_SPOOL" in u for u in posted_urls)

        asyncio.run(run())

    def test_ignores_deselection_events(self):
        async def run():
            hass = _make_hass()
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {})

            new_state = SimpleNamespace(
                entity_id="sensor.k2_abc_cfs_box_1_slot_0_filament",
                attributes={"selected": 0},
            )
            event = SimpleNamespace(data={
                "entity_id": "sensor.k2_abc_cfs_box_1_slot_0_filament",
                "new_state": new_state,
            })

            called = []
            with patch.object(sync, "_set_active_spool", side_effect=lambda id: called.append(id)):
                with patch.object(sync, "_clear_active_spool", side_effect=lambda: called.append("clear")):
                    await sync._on_slot_state_changed(event)

            assert called == []

        asyncio.run(run())
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tools/tests/test_spoolman_sync.py::TestSyncOptions tools/tests/test_spoolman_sync.py::TestActiveSlotWatcher -v
```

Expected: various `AttributeError` failures — methods don't exist yet.

- [ ] **Step 3: Implement `_sync_options`, `_on_slot_state_changed`, `_set_active_spool`, `_clear_active_spool` in `spoolman_sync.py`**

Add these imports at the top of `spoolman_sync.py`:

```python
import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # type: ignore[import]
from homeassistant.helpers.event import (  # type: ignore[import]
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.core import EVENT_HOMEASSISTANT_STARTED  # type: ignore[import]
from datetime import timedelta
```

Replace `async def async_setup(self) -> None: pass` with:

```python
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
```

Add `_slot_count` property and `_sync_options`:

```python
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
```

Add `_on_slot_state_changed`:

```python
    async def _on_slot_state_changed(self, event) -> None:
        """Handle state change on a CFS filament sensor; sync active spool if selected."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        selected = new_state.attributes.get("selected")
        if selected not in (1, True, "1"):
            return  # only act when a slot becomes active, not on deselection

        entity_id = event.data.get("entity_id", "")
        slot_index = self._entity_to_slot_index(entity_id)
        if slot_index is None:
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
```

Add Klipper REST helpers:

```python
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
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tools/tests/test_spoolman_sync.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_creality_ws_sm/spoolman_sync.py tools/tests/test_spoolman_sync.py
git commit -m "feat: implement SpoolmanSync async core (options sync + active spool watcher)"
```

---

## Task 5: Wire `SpoolmanSync` into the coordinator

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/coordinator.py`

- [ ] **Step 1: Import new constants and `SpoolmanSync` at the top of `coordinator.py`**

Add to the existing imports block in `coordinator.py`:

```python
from .const import (
    # ... existing imports ...
    CONF_SPOOLMAN_ENABLED,
    CONF_KLIPPER_PORT,
    CONF_SPOOLMAN_PREFIX,
    CONF_INPUT_SELECT_PREFIX,
    DEFAULT_KLIPPER_PORT,
    DEFAULT_SPOOLMAN_PREFIX,
    DEFAULT_INPUT_SELECT_PREFIX,
)
from .spoolman_sync import SpoolmanSync
```

- [ ] **Step 2: Add `_spoolman_sync` attribute in `__init__`**

In `KCoordinator.__init__`, after `self._is_k2_base: bool | None = None`, add:

```python
        self._spoolman_sync: SpoolmanSync | None = None
```

- [ ] **Step 3: Load Spoolman options in `_load_options`**

In `KCoordinator._load_options`, after the existing `self._polling_rate = options.get(...)` line, add:

```python
        # Spoolman sync config
        spoolman_enabled = options.get(CONF_SPOOLMAN_ENABLED, False)
        if spoolman_enabled:
            config = {
                "klipper_port": options.get(CONF_KLIPPER_PORT, DEFAULT_KLIPPER_PORT),
                "spoolman_prefix": options.get(CONF_SPOOLMAN_PREFIX, DEFAULT_SPOOLMAN_PREFIX),
                "input_select_prefix": options.get(CONF_INPUT_SELECT_PREFIX, DEFAULT_INPUT_SELECT_PREFIX),
            }
            if self._spoolman_sync is None:
                self._spoolman_sync = SpoolmanSync(self.hass, self, config)
                # async_setup is called from async_start after the WS connection is up
        else:
            if self._spoolman_sync is not None:
                self.hass.async_create_task(self._spoolman_sync.async_unload())
                self._spoolman_sync = None
```

- [ ] **Step 4: Start SpoolmanSync in `async_start`**

In `KCoordinator.async_start`, after `await self.client.start()`, add:

```python
        if self._spoolman_sync is not None:
            await self._spoolman_sync.async_setup()
```

- [ ] **Step 5: Unload SpoolmanSync in `async_stop`**

In `KCoordinator.async_stop`, before or after `await self.client.stop()`, add:

```python
        if self._spoolman_sync is not None:
            await self._spoolman_sync.async_unload()
```

- [ ] **Step 6: Run existing tests to verify nothing is broken**

```bash
python -m pytest tools/tests/ -v
```

Expected: all existing tests PASS (coordinator tests, model detection, utils, hygiene checks).

- [ ] **Step 7: Commit**

```bash
git add custom_components/ha_creality_ws_sm/coordinator.py
git commit -m "feat: integrate SpoolmanSync into coordinator lifecycle"
```

---

## Task 6: Config flow — Spoolman options

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/config_flow.py`
- Modify: `custom_components/ha_creality_ws_sm/strings.json`
- Modify: `custom_components/ha_creality_ws_sm/translations/en.json`

- [ ] **Step 1: Add new constants to `config_flow.py` imports**

In `config_flow.py`, add to the `from .const import (...)` block:

```python
    CONF_SPOOLMAN_ENABLED,
    CONF_KLIPPER_PORT,
    CONF_SPOOLMAN_PREFIX,
    CONF_INPUT_SELECT_PREFIX,
    DEFAULT_KLIPPER_PORT,
    DEFAULT_SPOOLMAN_PREFIX,
    DEFAULT_INPUT_SELECT_PREFIX,
```

- [ ] **Step 2: Read current Spoolman values in `async_step_init`**

In `OptionsFlowHandler.async_step_init`, after the existing `polling_rate = ...` line, add:

```python
        spoolman_enabled = self._entry.options.get(CONF_SPOOLMAN_ENABLED, False)
        klipper_port = self._entry.options.get(CONF_KLIPPER_PORT, DEFAULT_KLIPPER_PORT)
        spoolman_prefix = self._entry.options.get(CONF_SPOOLMAN_PREFIX, DEFAULT_SPOOLMAN_PREFIX)
        input_select_prefix = self._entry.options.get(CONF_INPUT_SELECT_PREFIX, DEFAULT_INPUT_SELECT_PREFIX)
```

- [ ] **Step 3: Add Spoolman fields to the schema in `async_step_init`**

At the end of `schema_dict.update({...})` block (after `CONF_MINUTES_TO_END_VALUE`), add:

```python
             vol.Optional(CONF_SPOOLMAN_ENABLED, default=spoolman_enabled): selector.BooleanSelector(),
             vol.Optional(CONF_KLIPPER_PORT, default=klipper_port): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
            ),
             vol.Optional(CONF_SPOOLMAN_PREFIX, default=spoolman_prefix): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
             vol.Optional(CONF_INPUT_SELECT_PREFIX, default=input_select_prefix): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            ),
```

- [ ] **Step 4: Sanitize `klipper_port` in the save handler**

In `async_step_init`, inside `if user_input is not None:`, after the `go2rtc_port` sanitization block, add:

```python
            if user_input.get(CONF_KLIPPER_PORT) is not None:
                try:
                    user_input[CONF_KLIPPER_PORT] = int(user_input[CONF_KLIPPER_PORT])
                except (ValueError, TypeError):
                    user_input[CONF_KLIPPER_PORT] = DEFAULT_KLIPPER_PORT
```

- [ ] **Step 5: Add labels to `strings.json`**

In the `"options" → "step" → "init" → "data"` object, add:

```json
          "spoolman_enabled": "Enable Spoolman Sync",
          "klipper_port": "Klipper API Port",
          "spoolman_prefix": "Spoolman Entity Prefix",
          "input_select_prefix": "Slot Input Select Prefix"
```

- [ ] **Step 6: Mirror to `translations/en.json`**

Make the same addition to `translations/en.json` (identical to `strings.json`).

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tools/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/ha_creality_ws_sm/config_flow.py \
        custom_components/ha_creality_ws_sm/strings.json \
        custom_components/ha_creality_ws_sm/translations/en.json
git commit -m "feat: add Spoolman options to integration config flow"
```

---

## Task 7: Frontend — spool dropdown in CFS card

**Files:**
- Modify: `custom_components/ha_creality_ws_sm/www/k_cfs_card.js`

This task has no automated tests (no JS test infra in the project). Manual verification steps are provided.

- [ ] **Step 1: Add dropdown CSS to the `style` string in `_render()`**

Inside the `const style = \`...\`` block in `_render()`, add after `.color-name { ... }` (around line 288):

```css
      .spool-select-wrap {
        width: 100%;
        margin-top: 8px;
      }

      .spool-select {
        width: 100%;
        background: rgba(var(--rgb-primary-text-color), 0.06);
        border: 1px solid rgba(var(--rgb-primary-text-color), 0.12);
        border-radius: 8px;
        color: var(--primary-text-color);
        font-size: 11px;
        padding: 4px 6px;
        cursor: pointer;
        appearance: none;
        -webkit-appearance: none;
        outline: none;
      }

      .spool-select:hover {
        background: rgba(var(--rgb-primary-text-color), 0.1);
        border-color: rgba(var(--rgb-primary-color), 0.4);
      }

      .spool-select option.separator {
        color: var(--secondary-text-color);
        font-style: italic;
      }
```

- [ ] **Step 2: Add `_getSpoolmanEntities()` helper method to `KCFSCard`**

Add before `_attachEventHandlers()`:

```javascript
  _getSpoolmanEntities(slotType) {
    const prefix = this._cfg.spoolman_prefix || "sensor.spoolman_spool_";
    const states = this._hass?.states || {};
    const matching = [];
    const other = [];

    Object.entries(states).forEach(([eid, st]) => {
      if (!eid.startsWith(prefix)) return;
      const suffix = eid.slice(prefix.length);
      if (!/^\d+$/.test(suffix)) return;

      const id = parseInt(suffix, 10);
      const rawName = st.attributes?.friendly_name || `Spool ${id}`;
      // Strip "Spoolman Spool N " prefix if present
      const stripPfx = `Spoolman Spool ${id} `;
      const cleanName = rawName.startsWith(stripPfx)
        ? rawName.slice(stripPfx.length).trim()
        : rawName;
      const option = `${id}: ${cleanName}`;
      const spoolType = (st.attributes?.filament_type || "").toUpperCase();

      if (slotType && spoolType && spoolType === slotType.toUpperCase()) {
        matching.push(option);
      } else {
        other.push(option);
      }
    });

    matching.sort((a, b) => parseInt(a) - parseInt(b));
    other.sort((a, b) => parseInt(a) - parseInt(b));
    return { matching, other };
  }
```

- [ ] **Step 3: Add `_renderSpoolDropdown()` method to `KCFSCard`**

Add before `_attachEventHandlers()`:

```javascript
  _renderSpoolDropdown(slot, globalSlotIndex) {
    if (!slot) return "";

    const safeType = slot.type && !["unknown", "unavailable", "—", "-"].includes(
      String(slot.type).toLowerCase()
    ) ? slot.type : null;
    const safeName = slot.name && !["unknown", "unavailable", "—", "-"].includes(
      String(slot.name).toLowerCase()
    ) ? slot.name : null;
    if (!safeType && !safeName) return ""; // empty slot — no dropdown

    const prefix = this._cfg.input_select_prefix || "input_select.cfs_slot_";
    const inputSelectId = `${prefix}${globalSlotIndex}`;
    const currentSelection = this._hass?.states?.[inputSelectId]?.state || "0: Niet in Spoolman";

    const { matching, other } = this._getSpoolmanEntities(safeType);

    let options = `<option value="0: Niet in Spoolman"${currentSelection === "0: Niet in Spoolman" ? " selected" : ""}>0: Niet in Spoolman</option>`;

    if (matching.length > 0) {
      const typeLabel = safeType || "Matching";
      options += `<option class="separator" disabled>── ${typeLabel} ──</option>`;
      matching.forEach(opt => {
        options += `<option value="${opt}"${currentSelection === opt ? " selected" : ""}>${opt}</option>`;
      });
    }

    if (other.length > 0) {
      options += `<option class="separator" disabled>──────────</option>`;
      other.forEach(opt => {
        options += `<option value="${opt}"${currentSelection === opt ? " selected" : ""}>${opt}</option>`;
      });
    }

    return `
      <div class="spool-select-wrap">
        <select class="spool-select" data-input-select="${inputSelectId}">
          ${options}
        </select>
      </div>
    `;
  }
```

- [ ] **Step 4: Compute the global slot index and call `_renderSpoolDropdown` in `_renderSpoolCard`**

`_renderSpoolCard` currently takes `(slot)`. Change it to `_renderSpoolCard(slot, globalSlotIndex)` and add the dropdown at the bottom of the returned HTML:

In the `_renderNormalMode` function, change:

```javascript
          ${selectedBox.slots.map((slot) => this._renderSpoolCard(slot)).join('')}
```

to:

```javascript
          ${selectedBox.slots.map((slot, idx) => {
            const globalIndex = (selectedBox.id - 1) * 4 + idx + 1;
            return this._renderSpoolCard(slot, globalIndex);
          }).join('')}
```

Then update `_renderSpoolCard` signature from `_renderSpoolCard(slot)` to `_renderSpoolCard(slot, globalSlotIndex = 1)` and append the dropdown call just before the closing `</div>` of the card:

```javascript
    return `
      <div class="spool-card ${isActive ? 'active' : ''}" data-eid="${slot.entity_id}">
        ${badge}
        <div class="ring-container">
          <div class="ring-outer" style="--spool-color: ${color}; --spool-pct: ${pct}%"></div>
          <div class="ring-inner">
            <span class="spool-pct">${pctDisplay}%</span>
            <span class="spool-label">${safeType}</span>
          </div>
        </div>
        <div class="material-name">${safeName}</div>
        <div class="color-name">${percentTextDisplay}</div>
        ${this._renderSpoolDropdown(slot, globalSlotIndex)}
      </div>
    `;
```

- [ ] **Step 5: Wire the dropdown `change` event in `_attachEventHandlers`**

In `_attachEventHandlers`, add after the existing spool card click handler block:

```javascript
    // Spool dropdown — write selection to input_select
    this._root.querySelectorAll('.spool-select').forEach(select => {
      const inputSelectId = select.dataset.inputSelect;
      if (!inputSelectId) return;

      select.onchange = (e) => {
        e.stopPropagation(); // prevent card click-through
        const selected = select.value;
        if (!this._hass || !inputSelectId) return;
        this._hass.callService('input_select', 'select_option', {
          entity_id: inputSelectId,
          option: selected,
        });
      };
    });
```

- [ ] **Step 6: Add new card config fields to `getStubConfig` and editor schema**

In `getStubConfig()`, add:

```javascript
      spoolman_prefix: "",
      input_select_prefix: "",
```

In `KCFSCardEditor._setupThemeForm()`, add to the `themeForm.schema` array:

```javascript
      { name: "spoolman_prefix", selector: { text: {} } },
      { name: "input_select_prefix", selector: { text: {} } },
```

And add labels in `themeForm.computeLabel`:

```javascript
      spoolman_prefix: "Spoolman Entity Prefix (leave empty for default)",
      input_select_prefix: "Slot Input Select Prefix (leave empty for default)",
```

- [ ] **Step 7: Manual verification checklist**

In Home Assistant with the updated card:
- [ ] Normal mode: each slot with filament data shows a spool dropdown
- [ ] Empty slots show no dropdown
- [ ] Compact/mini mode: no dropdowns visible
- [ ] Dropdown options show matching filament type first, then a separator, then others
- [ ] Selecting a spool updates the `input_select.cfs_slot_X` entity state
- [ ] Clicking the spool card itself still opens more-info (click does not trigger dropdown)

- [ ] **Step 8: Commit**

```bash
git add custom_components/ha_creality_ws_sm/www/k_cfs_card.js
git commit -m "feat: add Spoolman spool dropdown to CFS slot cards"
```

---

## Task 8: Final integration test & full test run

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tools/tests/ -v
```

Expected: all tests PASS. Fix any failures before proceeding.

- [ ] **Step 2: Run code hygiene check**

```bash
python -m pytest tools/tests/test_code_hygiene.py -v
python -m pytest tools/tests/test_no_blocking_calls.py -v
```

Expected: PASS. If `test_no_blocking_calls.py` flags `spoolman_sync.py`, verify there are no `time.sleep()` or synchronous I/O calls.

- [ ] **Step 3: End-to-end smoke test in Home Assistant**

With Spoolman HA integration installed and spools configured:
1. Go to HA → Integrations → Creality → Configure
2. Enable "Enable Spoolman Sync", set Klipper Port to your printer's port (default 4408)
3. Save — check logs for `SpoolmanSync: no Spoolman spool entities found` (if no spools yet) or successful `set_options` calls
4. Open CFS card in normal mode — verify dropdowns appear per slot
5. Select a spool — verify `input_select.cfs_slot_X` state changes in Developer Tools
6. With printer active and a slot printing, verify Klipper receives `SET_ACTIVE_SPOOL` (check `printer/gcode/script` in Klipper logs or via Moonraker)

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Spoolman integration complete — spool dropdown, options sync, Klipper REST"
```
