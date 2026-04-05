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
from types import SimpleNamespace


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
