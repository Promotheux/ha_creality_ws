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

# Stub homeassistant.helpers.event
ha_event_mod = types.ModuleType("homeassistant.helpers.event")
ha_event_mod.async_track_state_change_event = lambda *a, **k: (lambda: None)
ha_event_mod.async_track_time_interval = lambda *a, **k: (lambda: None)
sys.modules.setdefault("homeassistant.helpers.event", ha_event_mod)

# Stub homeassistant.core
ha_core_mod = types.ModuleType("homeassistant.core")
ha_core_mod.EVENT_HOMEASSISTANT_STARTED = "homeassistant_start"
sys.modules.setdefault("homeassistant.core", ha_core_mod)

# Stub homeassistant.helpers.aiohttp_client
ha_aiohttp_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp_mod.async_get_clientsession = lambda hass: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp_mod)

from custom_components.ha_creality_ws_sm.spoolman_sync import SpoolmanSync  # noqa: E402
from types import SimpleNamespace
import custom_components
import custom_components.ha_creality_ws_sm
import custom_components.ha_creality_ws_sm.spoolman_sync  # noqa: E402 — needed so patch() can resolve the dotted path
# Ensure the namespace package has its subpackage as an attribute (needed for unittest.mock.patch)
if not hasattr(custom_components, "ha_creality_ws_sm"):
    setattr(custom_components, "ha_creality_ws_sm", sys.modules["custom_components.ha_creality_ws_sm"])


def _make_state(entity_id: str, friendly_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id,
        attributes={"friendly_name": friendly_name},
    )


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


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_hass(spool_states=None, input_select_state=None):
    """Return a minimal hass stub for SpoolmanSync tests."""
    hass = MagicMock()

    # States
    all_states = spool_states or []
    hass.states.async_all.return_value = all_states
    hass.states.async_entity_ids.return_value = []

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
            sync = SpoolmanSync(hass, coord, {})
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
            sync = SpoolmanSync(hass, coord, {})
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

            new_state = SimpleNamespace(
                entity_id="sensor.k2_8fd7_cfs_box_1_slot_3_filament",
                attributes={"selected": 1},
            )
            event = SimpleNamespace(data={
                "entity_id": "sensor.k2_8fd7_cfs_box_1_slot_3_filament",
                "new_state": new_state,
            })

            posted_calls = []
            def fake_post(url, **kwargs):
                posted_calls.append(("post", url, kwargs.get("json", {})))
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

            assert any(
                method == "post" and isinstance(body, dict) and body.get("spool_id") == 3
                for method, _, body in posted_calls
            )

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

            posted_calls = []
            def fake_post(url, **kwargs):
                posted_calls.append(("post", url, kwargs.get("json", {})))
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

            assert any(
                method == "post" and "spoolman/spool_id" in url and body.get("spool_id") is None
                for method, url, body in posted_calls
            )

        asyncio.run(run())

    def test_ignores_deselection_events(self):
        async def run():
            hass = _make_hass()
            coord = _make_coordinator()
            sync = SpoolmanSync(hass, coord, {})
            sync._last_active_slot = 1  # pretend slot 1 was previously active

            new_state = SimpleNamespace(
                entity_id="sensor.k2_abc_cfs_box_1_slot_0_filament",
                attributes={"selected": 0},
            )
            event = SimpleNamespace(data={
                "entity_id": "sensor.k2_abc_cfs_box_1_slot_0_filament",
                "new_state": new_state,
            })

            called = []
            with patch.object(sync, "_set_active_spool", new=AsyncMock(side_effect=lambda id: called.append(id))):
                with patch.object(sync, "_clear_active_spool", new=AsyncMock(side_effect=lambda: called.append("clear"))):
                    await sync._on_slot_state_changed(event)

            assert called == []
            assert sync._last_active_slot is None  # debounce cleared on deselection

        asyncio.run(run())
