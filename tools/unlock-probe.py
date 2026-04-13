#!/usr/bin/env python3
"""
unlock-probe.py — find the minimum command sequence that unlocks the Geberit
device after an iPhone Geberit Home App session left it unresponsive.

The device accepts BLE connections and ACKs GATT writes but never responds
to GetSystemParameterList. The iPhone app goes through a longer initialization
sequence before GetSystemParameterList. This script probes each stage of that
sequence to find the minimum unlock.

Stages (run in order, stopping at first SUCCESS):
  0    Direct GetSystemParameterList (SINGLE, 8 indices)          (baseline — expect FAIL when stuck)
  0b   GetSystemParameterList iPhone-style (FIRST+CONS, 12 idx)  (key hypothesis: framing difference)
  1    Proc(0x01,0x13) args=020f0d0000 → GetSPL                 (the suspicious subscription call)
  2    All 4 × Proc(0x01,0x13)         → GetSPL
  2.5  Proc(0x01,0x11)×4 + Proc(0x01,0x13)×4  → GetSPL         (skip Proc_0x05 — it times out)
  3    UnknownProc_0x05 (optional) + Proc(0x01,0x11)×4 + Proc(0x01,0x13)×4 → GetSPL
  4    + GetStoredCommonSetting×10     → GetSPL
  5    Full iPhone init sequence       → GetSPL                  (guaranteed to work)
       (GetDeviceIdentification + GetSOCApplicationVersions + GetFirmwareVersionList
        + Proc_0x11×4 + Proc_0x13×4 + GetStoredProfileSetting×10
        + SetStoredProfileSetting×3 + GetStoredCommonSetting×10)

Note: UnknownProc_0x05 is marked optional in stages 3+ because it times out
on some stuck-device states. The stage continues past it rather than aborting.

Profile setting procedure codes: iOS wire uses 0x0A (get) / 0x0B (set),
not the C# enum values 0x53/0x54. This script uses the iOS wire format.

Usage (from repo root on the Raspberry Pi):
    python3 local-assets/unlock-probe.py
    python3 local-assets/unlock-probe.py --esphome 192.168.0.114 --mac 38:AB:41:2A:0D:67
"""

import asyncio
import sys
import os
import argparse
import logging
import configparser
from binascii import hexlify

# ── silence all verbose library output ──────────────────────────────────────
logging.basicConfig(level=logging.CRITICAL)
for _name in ["aioesphomeapi", "bleak", "asyncio", "aquaclean_console_app"]:
    logging.getLogger(_name).setLevel(logging.CRITICAL)

# Add no-op TRACE and SILLY levels so aquaclean imports don't crash
if not hasattr(logging, "TRACE"):
    logging.TRACE = 5
    logging.addLevelName(5, "TRACE")
    logging.Logger.trace = lambda self, msg, *a, **kw: None
if not hasattr(logging, "SILLY"):
    logging.SILLY = 4
    logging.addLevelName(4, "SILLY")
    logging.Logger.silly = lambda self, msg, *a, **kw: None

# ── path setup ───────────────────────────────────────────────────────────────
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import (
    AquaCleanBaseClient, BLEPeripheralTimeoutError,
)
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory


# ── generic raw-procedure call class ─────────────────────────────────────────
class RawProc:
    """Send any context/procedure/node with a fixed raw payload.

    first_cons=True: send as FIRST+CONS frame pair instead of SINGLE.
    Required for procedures whose response spans multiple frames
    (e.g. GetFirmwareVersionList 0x0E).
    """
    def __init__(self, context: int, procedure: int, node: int,
                 payload: bytes = b"", label: str = "",
                 first_cons: bool = False):
        self.api_call_attribute = ApiCallAttribute(context, procedure, node)
        self._payload = payload
        self.label = label or f"Proc(0x{context:02x},0x{procedure:02x})"
        self.first_cons = first_cons

    def get_api_call_attribute(self):
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        return self._payload

    def result(self, data: bytearray):
        return bytes(data)


# ── helpers ───────────────────────────────────────────────────────────────────
async def raw(bc: AquaCleanBaseClient, proc: RawProc) -> bytes:
    await bc.send_request(proc, send_as_first_cons=getattr(proc, "first_cons", False))
    return bytes(bc.message_context.result_bytes) if bc.message_context else b""


async def get_spl(bc: AquaCleanBaseClient) -> bool:
    """Try GetSystemParameterList. Returns True on success, False on timeout."""
    try:
        result = await bc.get_system_parameter_list_async([0, 1, 2, 3, 4, 5, 7, 9])
        vals = result.data_array if hasattr(result, "data_array") else result
        print(f"    GetSystemParameterList OK  → {vals}")
        return True
    except BLEPeripheralTimeoutError:
        print("    GetSystemParameterList TIMEOUT — device did not respond")
        return False


# ── stage sequences ───────────────────────────────────────────────────────────

# Proc(0x01,0x13) subscription calls observed from iPhone:
PROC_0x13 = [
    RawProc(0x01, 0x13, 0x01, bytes([0x04, 0x01, 0x03, 0x04, 0x05]), "Proc(0x01,0x13) 04,01,03,04,05"),
    RawProc(0x01, 0x13, 0x01, bytes([0x04, 0x06, 0x07, 0x08, 0x09]), "Proc(0x01,0x13) 04,06,07,08,09"),
    RawProc(0x01, 0x13, 0x01, bytes([0x04, 0x0a, 0x0b, 0x0c, 0x0e]), "Proc(0x01,0x13) 04,0a,0b,0c,0e"),
    RawProc(0x01, 0x13, 0x01, bytes([0x02, 0x0f, 0x0d, 0x00, 0x00]), "Proc(0x01,0x13) 02,0f,0d,00,00  ← suspicious"),
]

PROC_0x11 = [
    RawProc(0x01, 0x11, 0x01, bytes([0x04, 0x01, 0x03, 0x04, 0x05]), "Proc(0x01,0x11) 04,01,03,04,05"),
    RawProc(0x01, 0x11, 0x01, bytes([0x04, 0x06, 0x07, 0x08, 0x09]), "Proc(0x01,0x11) 04,06,07,08,09"),
    RawProc(0x01, 0x11, 0x01, bytes([0x04, 0x0a, 0x0b, 0x0c, 0x0e]), "Proc(0x01,0x11) 04,0a,0b,0c,0e"),
    RawProc(0x01, 0x11, 0x01, bytes([0x01, 0x0f, 0x00, 0x00, 0x00]), "Proc(0x01,0x11) 01,0f,00,00,00"),
]

# UnknownProc_0x05: "get available components" — no args; times out on some stuck states
PROC_0x05 = RawProc(0x01, 0x05, 0x01, b"", "UnknownProc_0x05")

# GetDeviceIdentification (context=0x00, proc=0x82) — no args
GET_DEV_ID = RawProc(0x00, 0x82, 0x01, b"", "GetDeviceIdentification")

# GetSOCApplicationVersions (context=0x01, proc=0x81) — no args
GET_SOC_VER = RawProc(0x01, 0x81, 0x01, b"", "GetSOCApplicationVersions")

# GetFirmwareVersionList (context=0x01, proc=0x0E) — 13-byte padded list, FIRST+CONS
GET_FW_VER = RawProc(
    0x01, 0x0E, 0x01,
    bytes([0x08, 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x00, 0x00, 0x00, 0x00]),
    "GetFirmwareVersionList",
    first_cons=True,
)

# GetStoredCommonSetting (proc=0x51) IDs seen in iPhone logs: 0–9
COMMON_SETTING_IDS = [0x02, 0x01, 0x03, 0x04, 0x06, 0x07, 0x05, 0x08, 0x00, 0x09]

# GetStoredProfileSetting — iOS wire format: proc=0x0A, 1-byte arg = setting index
# Order seen in iPhone log: AnalShowerPressure=2, OscillatorState=1, LadyShowerPressure=3,
#   AnalShowerPosition=4, WaterTemperature=6, WcSeatHeat=7, LadyShowerPosition=5,
#   DryerTemperature=8, OdourExtraction=0, DryerState=9
GET_PROFILE_SETTING_IDS = [2, 1, 3, 4, 6, 7, 5, 8, 0, 9]

# SetStoredProfileSetting — iOS wire format: proc=0x0B, 2-byte arg = [index, value]
# Values observed from iPhone: AnalShowerPressure=2, OscillatorState=2, LadyShowerPressure=2
SET_PROFILE_ARGS = [
    (2, 2, "AnalShowerPressure=2"),
    (1, 2, "OscillatorState=2"),
    (3, 2, "LadyShowerPressure=2"),
]

# GetSystemParameterList — iPhone wire format: proc=0x0D, FIRST+CONS, 12 indices
# iPhone requests [0,1,2,3,4,5,6,7,4,8,9,10] (index 4 appears twice — exact iPhone payload)
# Bridge normally sends 8 indices as SINGLE frame — this is the untested difference.
GET_SPL_IPHONE = RawProc(
    0x01, 0x0D, 0x01,
    bytes([0x0c, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x04, 0x08, 0x09, 0x0a]),
    "GetSystemParameterList (iPhone FIRST+CONS, 12 indices)",
    first_cons=True,
)


async def run_stage(bc: AquaCleanBaseClient, title: str, steps,
                    soft: set = None) -> bool:
    """Run a sequence of RawProc calls then attempt GetSystemParameterList.

    soft: set of proc labels that are allowed to TIMEOUT (stage continues past them).
    """
    soft = soft or set()
    print(f"\n  ── {title} ──")
    for proc in steps:
        name = proc.label if hasattr(proc, "label") else str(proc)
        try:
            result_bytes = await raw(bc, proc)
            print(f"    {name:55s}  OK  {hexlify(result_bytes[:12]).decode()}")
        except BLEPeripheralTimeoutError:
            if name in soft:
                print(f"    {name:55s}  TIMEOUT (optional, continuing)")
            else:
                print(f"    {name:55s}  TIMEOUT")
                return False
    return await get_spl(bc)


# ── connect helper ────────────────────────────────────────────────────────────
async def connect_and_probe(esphome_host, esphome_port, noise_psk,
                            device_id, stage_fn) -> bool:
    connector = BluetoothLeConnector(esphome_host, esphome_port, noise_psk)
    factory = AquaCleanClientFactory(connector)
    client = factory.create_client()
    try:
        await client.connect_ble_only(device_id)
        return await stage_fn(client.base_client)
    finally:
        try:
            await connector.disconnect()
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="Geberit unlock probe")
    parser.add_argument("--esphome", help="ESPHome proxy host (overrides config.ini)")
    parser.add_argument("--mac",     help="Geberit BLE MAC address (overrides config.ini)")
    parser.add_argument("--port",    type=int, default=6053)
    parser.add_argument("--psk",     default=None, help="ESPHome noise_psk (if set)")
    parser.add_argument("--local",   action="store_true",
                        help="Force local bleak (ignore ESPHome config)")
    args = parser.parse_args()

    # ── read config.ini ──────────────────────────────────────────────────────
    config = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    config_paths = [
        os.path.join(_repo_root, "aquaclean_console_app", "config.ini"),
    ]
    try:
        import aquaclean_console_app as _app
        config_paths.append(os.path.join(os.path.dirname(_app.__file__), "config.ini"))
    except Exception:
        pass
    config.read(config_paths)

    esphome_host = None if args.local else (args.esphome or config.get("ESPHOME", "host", fallback=None))
    esphome_port = args.port or int(config.get("ESPHOME", "port", fallback="6053"))
    noise_psk    = args.psk  or config.get("ESPHOME", "noise_psk", fallback=None) or None
    device_id    = args.mac  or config.get("BLE", "device_id", fallback=None)

    if not device_id:
        print("ERROR: need --mac <MAC> (or set [BLE] device_id in config.ini)")
        sys.exit(1)

    if esphome_host:
        print(f"Probe target : {device_id}  via ESP32 at {esphome_host}:{esphome_port}")
    else:
        print(f"Probe target : {device_id}  via local bleak (no ESPHome host)")
    print("Stages run until the first SUCCESS.\n")

    # ── Stage 0: bare GetSystemParameterList ─────────────────────────────────
    print("Stage 0 — bare GetSystemParameterList (confirm stuck state)")
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: get_spl(bc),
    )
    if ok:
        print("\nDevice is NOT stuck — GetSystemParameterList worked immediately.\n")
        return

    # ── Stage 0b: GetSystemParameterList as iPhone FIRST+CONS ────────────────
    # Hypothesis: device stuck state only accepts FIRST+CONS framing for proc 0x0D,
    # exactly as the iPhone sends it — bridge normally uses SINGLE framing.
    print("\nStage 0b — GetSystemParameterList iPhone-style (FIRST+CONS, 12 indices)")
    async def _stage_0b(bc):
        print(f"\n  ── GetSystemParameterList FIRST+CONS (no init) ──")
        try:
            result_bytes = await raw(bc, GET_SPL_IPHONE)
            print(f"    {GET_SPL_IPHONE.label:55s}  OK  {hexlify(result_bytes[:12]).decode()}")
            return True
        except BLEPeripheralTimeoutError:
            print(f"    {GET_SPL_IPHONE.label:55s}  TIMEOUT")
            return False

    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        _stage_0b,
    )
    if ok:
        print("\n✓ UNLOCK: FIRST+CONS GetSystemParameterList alone is sufficient.")
        print("  Fix: change bridge GetSystemParameterList to use FIRST+CONS framing.\n")
        return

    # ── Stage 1: only the suspicious Proc(0x01,0x13) 020f0d0000 ─────────────
    print("\nStage 1 — Proc(0x01,0x13) args=020f0d0000 only")
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc, "Proc(0x01,0x13) 020f0d0000 only",
                             [PROC_0x13[-1]]),
    )
    if ok:
        print("\n✓ UNLOCK: Proc(0x01,0x13) args=020f0d0000 is sufficient.\n")
        return

    # ── Stage 2: all 4 × Proc(0x01,0x13) ────────────────────────────────────
    print("\nStage 2 — all 4 × Proc(0x01,0x13)")
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc, "all Proc(0x01,0x13) ×4", PROC_0x13),
    )
    if ok:
        print("\n✓ UNLOCK: all Proc(0x01,0x13) ×4 is sufficient.\n")
        return

    # ── Stage 2.5: Proc(0x01,0x11)×4 + Proc(0x01,0x13)×4  (no Proc_0x05) ──
    print("\nStage 2.5 — Proc(0x01,0x11)×4 + Proc(0x01,0x13)×4  (skipping Proc_0x05)")
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc, "Proc_0x11×4 + Proc_0x13×4",
                             PROC_0x11 + PROC_0x13),
    )
    if ok:
        print("\n✓ UNLOCK: Proc_0x11×4 + Proc_0x13×4 is sufficient.\n")
        return

    # ── Stage 3: Proc_0x05 (optional) + Proc_0x11×4 + Proc_0x13×4 ──────────
    print("\nStage 3 — UnknownProc_0x05 (optional) + Proc(0x01,0x11)×4 + Proc(0x01,0x13)×4")
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc, "Proc_0x05 + Proc_0x11×4 + Proc_0x13×4",
                             [PROC_0x05] + PROC_0x11 + PROC_0x13,
                             soft={"UnknownProc_0x05"}),
    )
    if ok:
        print("\n✓ UNLOCK: Proc_0x05 + Proc_0x11×4 + Proc_0x13×4 is sufficient.\n")
        return

    # ── Stage 4: + GetStoredCommonSetting ×10 ────────────────────────────────
    print("\nStage 4 — + GetStoredCommonSetting ×10")
    common_settings = [
        RawProc(0x01, 0x51, 0x01, bytes([sid]), f"GetStoredCommonSetting(id={sid:#04x})")
        for sid in COMMON_SETTING_IDS
    ]
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc,
                             "Proc_0x05 + Proc_0x11×4 + Proc_0x13×4 + GetStoredCommonSetting×10",
                             [PROC_0x05] + PROC_0x11 + PROC_0x13 + common_settings,
                             soft={"UnknownProc_0x05"}),
    )
    if ok:
        print("\n✓ UNLOCK: including GetStoredCommonSetting is sufficient.\n")
        return

    # ── Stage 5: full iPhone sequence (should always work) ───────────────────
    print("\nStage 5 — full iPhone init sequence")
    print("  (GetDeviceId + GetSOCVer + GetFWVer + Proc_0x11×4 + Proc_0x13×4")
    print("   + GetStoredProfileSetting×10 + SetStoredProfileSetting×3 + GetStoredCommonSetting×10)")

    # GetStoredProfileSetting: iOS wire proc=0x0A, 1-byte payload = setting index
    get_profile = [
        RawProc(0x01, 0x0A, 0x01, bytes([sid]),
                f"GetStoredProfileSetting(0x0A, id={sid})")
        for sid in GET_PROFILE_SETTING_IDS
    ]
    # SetStoredProfileSetting: iOS wire proc=0x0B, 2-byte payload = [index, value]
    set_profile = [
        RawProc(0x01, 0x0B, 0x01, bytes([idx, val]),
                f"SetStoredProfileSetting(0x0B, {desc})")
        for idx, val, desc in SET_PROFILE_ARGS
    ]
    full_sequence = (
        [GET_DEV_ID, GET_SOC_VER, GET_FW_VER]
        + [PROC_0x05] + PROC_0x11 + PROC_0x13
        + get_profile + set_profile + common_settings
    )
    ok = await connect_and_probe(
        esphome_host, esphome_port, noise_psk, device_id,
        lambda bc: run_stage(bc, "full iPhone init sequence", full_sequence,
                             soft={"UnknownProc_0x05"}),
    )
    if ok:
        print("\n✓ UNLOCK: full iPhone sequence worked.\n")
    else:
        print("\n✗ FAILED: even the full iPhone sequence could not unlock the device.")
        print("  Device likely needs a power cycle.\n")


if __name__ == "__main__":
    asyncio.run(main())
