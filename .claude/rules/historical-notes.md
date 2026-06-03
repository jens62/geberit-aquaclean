# Historical Notes

## Feature summary (merged from `feature/persistent-esphome-api`)

Key additions vs. the original `main`:
- Split connect timing (ESP32 API ms vs BLE ms)
- Runtime toggle for `ble_connection` at runtime
- On-demand polling loop with `set_poll_interval` support (both modes)
- First-poll identification fetch + SSE caching
- `_poll_interval_event` in ServiceMode for persistent-mode interval changes
- `_on_poll_done()` resets connect timing to 0 (persistent BLE mode reuses connection)
- `_check_config_errors()` — startup config validation stub
- `--command check-config` CLI command — returns JSON
- Recovery fallback fixes: `wait_for_device_restart` now passes `bluetooth_connector`
  so the persistent `_esphome_api` is reused; MQTT topic bug fixed; E2005 now surfaces
  via MQTT + webapp SSE; E2003/E2004 now published to correct error topic
- Error code hints: all `ErrorCode` definitions carry user-facing `hint` text;
  `doc_url` field reserved for future doc links
- `soc_application_versions = None` initialised in `AquaCleanClient.__init__`
- Cached-path timing: `get_identification()` / `get_initial_operation_date()` include
  timing zeros when returning from cache
- Circuit breaker in `_polling_loop`: after 5 consecutive failures switches to 60s
  probe interval; resets `_identification_fetched` on recovery
- MQTT `reconnect()` latent bug fixed: `on_disconnect` uses `run_coroutine_threadsafe`
- Startup version logging: `importlib.metadata.version("geberit-aquaclean")`
- On-demand poll errors now surface to webapp via SSE
- `esphome_proxy_error_hint` stale-hint fix: `_update_esphome_proxy_state` auto-clears
  `error_hint` when `error_code="E0000"`

---

## haggis dependency removed (2026-02-23)

`haggis` was used only for `add_logging_level`. The patched fork became incompatible
with Python 3.13 (`_acquireLock` removed). Replaced with a 10-line inline
`_add_logging_level()` in `main.py` — no external dependency, works on all Python versions.

---

## ESPHome BLE connection — probe results (2026-02-21)

All 4 parameter combinations tested against ESPHome 2026.1.5 from Mac (192.168.0.87):

| has_cache | address_type | Protocol | Result |
|-----------|--------------|----------|--------|
| False | 0 PUBLIC | CONNECT_V3_WITHOUT_CACHE | OK MTU=23 |
| True | 0 PUBLIC | CONNECT_V3_WITH_CACHE | OK MTU=23 |
| False | 1 RANDOM | CONNECT_V3_WITHOUT_CACHE | OK MTU=23 |
| True | 1 RANDOM | CONNECT_V3_WITH_CACHE | OK MTU=23 |

**The connection parameters in `ESPHomeAPIClient.py` are correct.**
Current settings (`has_cache=False, address_type=0, feature_flags=<device actual>`) work.

`aioesphomeapi` source confirms only two code paths:
- `has_cache=True` → `CONNECT_V3_WITH_CACHE`
- `has_cache=False` + REMOTE_CACHING bit set → `CONNECT_V3_WITHOUT_CACHE`
- `feature_flags=0` raises `ValueError`.

**The actual bug found — `UnsubscribeBluetoothLEAdvertisementsRequest` while BLE is active:**
`unsub_adv()` is synchronous: it only QUEUES the frame in aioesphomeapi's internal send
buffer. Calling `unsub_adv()` while BLE is active causes the ESP32 to disconnect the BLE
client. Fix: store as `self._esphome_unsub_adv` and call it in `disconnect()` AFTER
`await self.client.disconnect()` tears down the BLE link.

**Fix verified — Kali production run 2026-02-21 18:01:** all BLE connects succeed,
all disconnects clean (reason=0x00), full data flow confirmed, REST and MQTT stable.

---

## External references

| Resource | URL |
|----------|-----|
| Geberit AquaClean Mera Comfort — Service Manual (PDF) | https://cdn.data.geberit.com/documents-a6/972.447.00.0_00-A6.pdf |
| thomas-bingel C# reference repo | https://github.com/thomas-bingel/geberit-aquaclean |
