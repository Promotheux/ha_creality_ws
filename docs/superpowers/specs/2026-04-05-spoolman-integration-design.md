# Spoolman Integration Design

**Date:** 2026-04-05  
**Status:** Approved

## Overview

Add native Spoolman spool assignment to the CFS card. Each slot in the CFS card gets a dropdown to pick the corresponding Spoolman spool. The plugin backend watches for active slot changes and calls the Klipper REST API to set/clear the active spool — replacing two existing HA automations.

**Automations replaced:**
1. "Update CFS Dropdown Options from Spoolman" — plugin handles on startup and hourly
2. "Spoolman: Sync Active Spool from K2" — plugin handles via state change watcher

## Architecture

```
k_cfs_card.js (frontend)
  • Spool dropdown per slot card (normal mode only)
  • Reads hass.states for sensor.spoolman_spool_* entities
  • Filters by filament type: matching type shown first, rest below separator
  • Writes selection via input_select.select_option service call
  • Config: spoolman_prefix, input_select_prefix
        │
        │ hass.callService
        ▼
input_select.cfs_slot_1..N  (existing HA helper entities)
  • Options: ["0: Niet in Spoolman", "3: PETG White", ...]
  • State: currently assigned spool for that slot
        │
        │ read by
        ▼
spoolman_sync.py (new Python module)
  • On HA start + hourly: scan Spoolman sensors → input_select.set_options
  • On slot selected attribute change: read input_select → POST to Klipper REST
```

## Files Changed

| File | Change |
|------|--------|
| `custom_components/ha_creality_ws/www/k_cfs_card.js` | Add spool dropdown to slot cards |
| `custom_components/ha_creality_ws/spoolman_sync.py` | New module — options sync + active spool watcher |
| `custom_components/ha_creality_ws/coordinator.py` | Instantiate SpoolmanSync, pass config |
| `custom_components/ha_creality_ws/config_flow.py` | Add Spoolman options to the options flow |
| `custom_components/ha_creality_ws/const.py` | New constants for Spoolman config keys |
| `custom_components/ha_creality_ws/strings.json` | UI labels for new config fields |
| `custom_components/ha_creality_ws/translations/en.json` | English translations for new fields |

## Frontend: Spool Dropdown

### Behavior

- Shown on each configured slot card in **normal mode only** (compact/mini mode excluded — too small)
- Hidden when a slot has no filament data (empty slot, `hasFilament === false`)
- The dropdown is a native `<select>` element styled to match the card theme

### Options Population

Built fresh on each `_update()` call from `this._hass.states`:

```
1. Collect all entities matching spoolman_prefix + numeric suffix
   (default prefix: "sensor.spoolman_spool_")
2. Sort by spool ID (numeric)
3. Split into two groups vs. slot.type (case-insensitive):
   - Group A: filament_type attribute matches slot type (e.g., "PETG")
   - Group B: everything else
4. Build options array:
   ["0: Niet in Spoolman",
    "── PETG ──",           ← separator label (not selectable)
    "3: PETG White",
    "7: PETG Black",
    "──────────",           ← separator
    "1: PLA Red",
    "12: ABS Grey"]
```

Option value format: `"{id}: {friendly_name}"` — strips the Spoolman prefix from `friendly_name` (e.g. "Spoolman Spool " removed).

### Current Selection

Read from `hass.states[input_select_entity]?.state` where `input_select_entity` is derived as:
```
{input_select_prefix}{slot_global_index}
```
- `input_select_prefix` defaults to `input_select.cfs_slot_`
- `slot_global_index` is 1-based, incremented per configured slot across all boxes (box0_slot0=1, box0_slot1=2, box0_slot2=3, box0_slot3=4, box1_slot0=5, …)

### On Change

```javascript
hass.callService('input_select', 'select_option', {
  entity_id: input_select_entity,
  option: selectedValue,   // e.g. "3: PETG White"
});
```

### Card Config Schema Additions

```javascript
{ name: "spoolman_prefix",      selector: { text: {} } }  // default: "sensor.spoolman_spool_"
{ name: "input_select_prefix",  selector: { text: {} } }  // default: "input_select.cfs_slot_"
```

Both added to the "Theme" tab in the card editor (they are optional overrides, not required).

## Backend: `spoolman_sync.py`

### Class: `SpoolmanSync`

Instantiated by `KCoordinator.__init__` when `spoolman_enabled` is true in config entry options.

```python
class SpoolmanSync:
    def __init__(self, hass, coordinator, config: dict):
        self._hass = hass
        self._coordinator = coordinator
        self._klipper_port = config.get("klipper_port", 4408)
        self._spoolman_prefix = config.get("spoolman_prefix", "sensor.spoolman_spool_")
        self._input_select_prefix = config.get("input_select_prefix", "input_select.cfs_slot_")
        self._last_active_slot = None
        self._unsub_listeners = []

    async def async_setup(self): ...      # register listeners, schedule sync
    async def async_unload(self): ...     # cancel listeners and scheduled tasks
    async def _sync_options(self, _=None): ...  # populate input_select options
    async def _on_slot_state_changed(self, event): ...  # handle selected→1
    async def _set_active_spool(self, spool_id: int): ...  # call Klipper REST
    async def _clear_active_spool(self): ...
```

### Options Sync (`_sync_options`)

Runs on: `EVENT_HOMEASSISTANT_STARTED` + `async_track_time_interval` every 3600 seconds.

```python
# 1. Collect Spoolman entities
spools = [
    (int(eid.removeprefix(prefix)), state.attributes.get("friendly_name", ""))
    for eid, state in hass.states.async_all()
    if eid.startswith(prefix) and eid.removeprefix(prefix).isdigit()
]
spools.sort(key=lambda x: x[0])

# 2. Build options list
options = ["0: Niet in Spoolman"] + [f"{id}: {clean_name}" for id, clean_name in spools]

# 3. Set options on all configured input_select entities
# Count configured slots from coordinator data (boxsInfo.materialBoxs)
for slot_index in range(1, total_slots + 1):
    entity_id = f"{input_select_prefix}{slot_index}"
    await hass.services.async_call(
        "input_select", "set_options",
        {"entity_id": entity_id, "options": options},
        blocking=False,
    )
```

If an `input_select` entity does not exist, log a warning and skip (user must create them via HA Helpers UI or configuration.yaml). Users migrating from the existing automation already have `input_select.cfs_slot_1` through `cfs_slot_4` and no action is needed.

### Active Slot Watcher (`_on_slot_state_changed`)

Registers a state change listener on `sensor.*_cfs_box_*_slot_*_filament` entities (matched by pattern using `async_track_state_change_event`).

```python
async def _on_slot_state_changed(self, event):
    new_state = event.data.get("new_state")
    if not new_state:
        return
    selected = new_state.attributes.get("selected")
    if selected not in (1, True, "1"):
        return  # only act when a slot becomes active

    # Derive slot global index from entity_id
    # e.g. "sensor.k2_8fd7_cfs_box_1_slot_3_filament" → box_id=1, slot_id=3
    entity_id = event.data["entity_id"]
    slot_index = self._entity_to_slot_index(entity_id)
    if slot_index is None:
        return

    if slot_index == self._last_active_slot:
        return  # no change
    self._last_active_slot = slot_index

    # Read corresponding input_select
    input_entity = f"{self._input_select_prefix}{slot_index}"
    input_state = self._hass.states.get(input_entity)
    if not input_state:
        _LOGGER.warning("SpoolmanSync: input_select %s not found", input_entity)
        return

    selection = input_state.state  # e.g. "3: PETG White"
    try:
        spool_id = int(selection.split(":")[0]) if ":" in selection else 0
    except (ValueError, AttributeError):
        spool_id = 0

    if spool_id > 0:
        await self._set_active_spool(spool_id)
    else:
        await self._clear_active_spool()
```

### Slot Index Derivation

```python
def _entity_to_slot_index(self, entity_id: str) -> int | None:
    """Map sensor.*_cfs_box_{B}_slot_{S}_filament to a 1-based global slot index."""
    import re
    m = re.search(r"_cfs_box_(\d+)_slot_(\d+)_filament$", entity_id)
    if not m:
        return None
    box_id = int(m.group(1))
    slot_id = int(m.group(2))
    # Global index: each box has 4 slots
    return box_id * 4 + slot_id + 1
```

> **Note:** Based on the observed entity ID `sensor.k2_8fd7_cfs_box_1_slot_3_filament`, box IDs from WS data start at 1. The correct formula is `(box_id - 1) * 4 + slot_id + 1`. Verify during implementation by inspecting `boxsInfo.materialBoxs[].id` in coordinator data.

### Klipper REST Calls

```python
async def _set_active_spool(self, spool_id: int):
    host = self._coordinator.client._host
    url = f"http://{host}:{self._klipper_port}/printer/gcode/script"
    params = {"script": f"SET_ACTIVE_SPOOL ID={spool_id}"}
    session = async_get_clientsession(self._hass)
    try:
        async with session.post(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                _LOGGER.warning("SpoolmanSync: Klipper returned %s", resp.status)
    except Exception as e:
        _LOGGER.error("SpoolmanSync: Failed to call Klipper: %s", e)

async def _clear_active_spool(self):
    # Same as above but script="CLEAR_ACTIVE_SPOOL"
```

## Integration Config: New Options

Added to the **options flow** (not the initial config flow — avoids requiring Spoolman for basic setup).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `spoolman_enabled` | bool | `false` | Enable Spoolman sync |
| `klipper_port` | int | `4408` | Klipper API port on printer host |
| `spoolman_prefix` | str | `sensor.spoolman_spool_` | Entity ID prefix for Spoolman spools |
| `input_select_prefix` | str | `input_select.cfs_slot_` | Entity ID prefix for slot assignments |

The options flow adds a new "Spoolman" step shown after the existing options. When `spoolman_enabled` is toggled off, `SpoolmanSync` is unloaded but `input_select` entities are left as-is.

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Spoolman entities not found | Options list shows only "0: Niet in Spoolman"; logs warning |
| `input_select` entity missing | `_sync_options` logs warning and skips that slot; `_on_slot_state_changed` logs warning and skips Klipper call |
| Klipper REST call fails | Logs error; no retry (next active slot change will re-trigger) |
| Printer offline | Klipper call throws; caught and logged |
| No spool assigned (selection = "0: Niet in Spoolman") | Calls `CLEAR_ACTIVE_SPOOL` |

## Out of Scope

- Creating `input_select` entities automatically — user creates them via HA Helpers UI
- Compact/mini mode dropdown — card too small, omitted by design
- Spoolman weight/remaining tracking on the card — separate feature
- Supporting non-Klipper printer backends
