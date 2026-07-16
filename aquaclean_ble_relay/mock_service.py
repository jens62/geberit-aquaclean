"""
mock_service.py — single thin CLI entry point for mocking Geberit AquaClean devices.

Phase 5 (docs/developer/mock-service-requirements.md §1/§2/§3): multiple
--device entries now run concurrently in one process, one asyncio task each.

--model is a single, open-ended lookup table (§3, decided — not a separate
--protocol + --model split): _MODEL_REGISTRY maps a model name to its class
plus that model's sensible defaults (e.g. Alba's own constructor defaults to
mode="unsupported" — faithful to the original script's default, which
deliberately tests the HACS unsupported-device screen — but nobody saying
"mock an Alba" through this orchestrator wants that by default, they want the
functional protocol; the registry's default overrides that to mode="ble20"
while leaving AlbaMock itself untouched). Explicit --device fields always win
over a model's registry defaults. Add new models/variants (e.g. "sela" once
its mock exists — Phase 8) by adding one entry here; mock_service.py itself
never branches on protocol.

Multiple devices CAN share one adapter at the D-Bus/GATT level — BlueZ
supports multiple GATT applications and multiple advertisement instances per
adapter (confirmed: this VM's adapter reports SupportedInstances=3). What's
rejected is two --device entries with the exact same (model, adapter) pair,
since that would have both instances registering under identical D-Bus
object paths. Two mock/protocol-level fixes were needed to make sharing an
adapter safe at that level, both applied directly in
mera_mock.py/alba_mock.py, not here:
  1. D-Bus GATT app paths are now prefixed by model name AND adapter — they
     used to be tagged by adapter only, so Mera's and Alba's generically-named
     "battery"/"dis" service paths would collide if they ever shared an
     adapter.
  2. MeraMock's _emit_interface_added suppression (needed to work around a
     bluez_peripheral/BlueZ characteristic-registration race) used to patch
     dbus_next.message_bus.BaseMessageBus at the CLASS level — process-wide,
     so two concurrent registrations would race each other's patch/restore.
     Now scoped to each instance's own `bus` object.

BUT: sharing one adapter means sharing that adapter's BLE MAC address — every
advertisement instance registered on it transmits from the same address
unless something explicitly configures per-instance private addressing
(bluez_peripheral's simple Advertisement object doesn't). Two devices sharing
an adapter will very likely broadcast two different payloads from the
identical MAC simultaneously, which the Geberit Home App's MAC-keyed device
list (see docs/developer/mock-geberit-alba.md gotcha #5) will likely find
just as confusing as it found the sequential version of this problem in
Phase 4 — possibly more so. Sharing an adapter is fine for testing the mocks'
own GATT/protocol/persistence correctness via direct-connect tooling; use two
physically separate adapters (two distinct MACs) when the point of the test
is the real Home App discovering two devices independently.

All requested adapters are validated to actually exist (one throwaway D-Bus
connection, before any device starts) — a typo'd --adapter now fails fast for
every device in the batch, rather than only the affected one failing deep
inside GATT registration.

Web UI ports collide the same way adapters could: both MeraMock and AlbaMock
default web_port=8765 and each binds a real TCP listener there. With more
than one --device, every one must specify an explicit, distinct web_port —
checked at parse time (asyncio would otherwise raise "address already in
use" deep inside uvicorn/aiohttp startup, one device silently failing while
the other keeps running).

Logging (Phase 7, docs/developer/mock-service-requirements.md §7): each mock
configures its own (model, adapter) logger from the shared state_dir via
aquaclean_ble_relay/mock_logging.py — console, a per-device file, and one
combined file shared by every device logger in this process. mock_service.py
itself doesn't touch logging at all beyond passing state_dir through
(_resolve_kwargs already defaults it onto every device's kwargs) — no more
process-wide stdout/stderr tee, since every device's own combined-file
handler now covers cross-device correlation properly.
"""

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

from aquaclean_ble_relay.mera_mock import MeraMock
from aquaclean_ble_relay.alba_mock import AlbaMock
from aquaclean_ble_relay.mock_bluez_adapter import select_adapter

if "dbus_fast" in sys.modules:
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType
else:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType

_MODEL_REGISTRY = {
    "mera": {"cls": MeraMock, "defaults": {}},
    "alba": {"cls": AlbaMock, "defaults": {"mode": "ble20"}},
}


def _coerce(value: str):
    """--device field values arrive as strings; coerce numeric-looking ones
    (web_port, send_delay_sec) so they reach the model constructor as the
    right type. Everything else (adapter, mode, model) stays a string."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_device_spec(spec: str) -> dict:
    """Parse one --device value: comma-separated key=value pairs, e.g.
    "model=alba,adapter=hci0,mode=ble20,web_port=8765". Fails at parse time
    (argparse.ArgumentTypeError) on a malformed field or unknown model,
    rather than after connecting to D-Bus.

    Deliberately generic beyond 'model': every other field is passed straight
    through as a constructor kwarg to whichever class model_name maps to
    (main() reports a clear error if a given model doesn't accept a given
    field) — this is what lets mode=/send_delay_sec= reach AlbaMock and
    web_port=/state_dir= reach either model without mock_service.py needing
    to know each model's exact parameter list.
    """
    fields = {}
    for part in spec.split(","):
        if "=" not in part:
            raise argparse.ArgumentTypeError(
                f"--device: malformed field {part!r} in {spec!r} (expected key=value)"
            )
        key, _, value = part.partition("=")
        fields[key.strip()] = _coerce(value.strip())
    if "model" not in fields:
        raise argparse.ArgumentTypeError(f"--device {spec!r}: missing required 'model' field")
    if fields["model"] not in _MODEL_REGISTRY:
        available = ", ".join(sorted(_MODEL_REGISTRY))
        raise argparse.ArgumentTypeError(
            f"--device {spec!r}: unknown model {fields['model']!r} — available: {available}"
        )
    return fields


def _resolve_kwargs(spec: dict, state_dir: Path) -> tuple[str, dict]:
    """One --device spec -> (model_name, constructor_kwargs), with registry
    defaults applied under explicit fields and state_dir filled in if absent."""
    spec = dict(spec)
    model_name = spec.pop("model")
    entry = _MODEL_REGISTRY[model_name]
    kwargs = dict(entry["defaults"])
    kwargs.update(spec)  # explicit --device fields always win over model defaults
    kwargs.setdefault("state_dir", str(state_dir))
    return model_name, kwargs


async def _check_adapters_exist(adapter_names: set[str]) -> None:
    """Fail fast, before starting any device, if a named adapter doesn't
    exist — one throwaway D-Bus connection shared across all the names in
    this batch, rather than each device discovering its own typo deep inside
    GATT registration."""
    if not adapter_names:
        return
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        for name in adapter_names:
            await select_adapter(bus, name)  # raises ValueError if not found
    finally:
        bus.disconnect()


async def _run_all(mocks) -> None:
    await asyncio.gather(*(m.run() for m in mocks))


def _start_capture(cmd: list[str], log_path: Path, tee_to_file: bool = False):
    """Start a sudo-prefixed background capture process (btmon, bluetoothd -n -d)
    before any mock device starts. Returns (Popen, log_file_or_None) for
    _stop_capture() to clean up later. tee_to_file redirects the process's own
    stdout/stderr into log_path — used for bluetoothd, which has no -w-style
    output flag of its own; btmon writes log_path itself via its own -w arg."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if tee_to_file:
        log_file = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)
        return proc, log_file
    proc = subprocess.Popen(cmd)
    return proc, None


def _stop_capture(label: str, proc: subprocess.Popen, log_file, match_arg: str) -> None:
    """Terminate a sudo-launched capture process. sudo doesn't always forward
    SIGTERM to its child reliably depending on version/config, so terminate()
    is backed by a `sudo pkill -f` fallback keyed on a distinguishing argument
    (e.g. the process's own output path, or a flag combination unique to our
    invocation) so cleanup is robust even if the direct terminate() doesn't
    reach the actual btmon/bluetoothd process."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    subprocess.run(
        ["sudo", "pkill", "-f", match_arg],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if log_file:
        log_file.close()
    print(f"[mock_service] stopped {label}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mock_service.py",
        description="Single thin CLI entry point for mocking Geberit AquaClean devices.",
    )
    parser.add_argument(
        "--device", action="append", dest="devices", type=_parse_device_spec,
        metavar="model=NAME,adapter=HCI[,web_port=PORT][,mode=MODE][,send_delay_sec=SEC]",
        help="One mocked device. Repeatable — each runs concurrently as its own "
             "asyncio task. Required field: model (one of: %s — see --list-models "
             "for each model's defaults). Every other field is passed straight "
             "through to that model's constructor, overriding the model's registry "
             "defaults — e.g. adapter, web_port (int), mode/send_delay_sec (Alba "
             "only). Two --device entries may share one adapter (BlueZ supports "
             "multiple GATT apps per adapter) but not the same (model, adapter) pair."
             % ", ".join(sorted(_MODEL_REGISTRY)),
    )
    parser.add_argument(
        "--state-dir", default=None, metavar="DIR",
        help="Directory for the shared persistence DB and auto-named log file "
             "(default: aquaclean_ble_relay/mock_state/, alongside this script).",
    )
    parser.add_argument(
        "--list-models", action="store_true",
        help="List registered --device model= values and exit.",
    )
    parser.add_argument(
        "--btmon-capture", action="store_true",
        help="Start 'sudo btmon -w <state-dir>/logs/mock-btmon_<timestamp>.btsnoop' "
             "before any device starts, and stop it on exit.",
    )
    parser.add_argument(
        "--bluetoothd-debug", action="store_true",
        help="Start 'sudo bluetoothd -n -d --noplugin=battery' before any device "
             "starts, redirected to <state-dir>/logs/mock-bluetoothd-debug_<timestamp>.log, "
             "and stop it on exit. Does NOT stop/restart the systemd bluetooth service — "
             "if it's already running and holding the D-Bus name, this will simply fail "
             "to bind, same as running the command by hand.",
    )
    args = parser.parse_args()

    if args.list_models:
        for name in sorted(_MODEL_REGISTRY):
            defaults = _MODEL_REGISTRY[name]["defaults"]
            suffix = f" (defaults: {defaults})" if defaults else ""
            print(f"{name}{suffix}")
        return

    if not args.devices:
        parser.error("--device is required (or use --list-models)")

    seen_pairs = set()
    for spec in args.devices:
        pair = (spec["model"], spec.get("adapter"))
        if pair in seen_pairs:
            model_name, adapter = pair
            parser.error(
                f"duplicate --device: model={model_name},adapter={adapter!r} given more "
                "than once — two devices can share an adapter, but not with the same model "
                "(their D-Bus paths would collide)"
            )
        seen_pairs.add(pair)

    state_dir = Path(args.state_dir) if args.state_dir else Path(__file__).parent / "mock_state"
    resolved = [_resolve_kwargs(spec, state_dir) for spec in args.devices]

    if len(resolved) > 1:
        # Both MeraMock and AlbaMock default web_port=8765 and each binds a real TCP
        # listener there (Mera always; Alba whenever mode=="ble20", the registry
        # default) — two devices left at the default would collide with
        # "address already in use" deep inside asyncio, not at parse time. Require
        # every device to state an explicit, distinct port once there's more than one.
        missing = [i for i, (_, kwargs) in enumerate(resolved) if "web_port" not in kwargs]
        if missing:
            parser.error(
                f"--device entr{'y' if len(missing) == 1 else 'ies'} {missing} missing "
                "web_port= — every device needs an explicit, distinct web_port when "
                "running more than one at once (both models default to 8765 and would "
                "otherwise collide binding the same port)"
            )
        ports = [kwargs["web_port"] for _, kwargs in resolved]
        if len(set(ports)) != len(ports):
            parser.error(f"--device web_port values must be distinct, got: {ports}")

    # Each mock configures its own (model, adapter) logger from state_dir
    # (console + per-device file + one combined file shared by every device
    # logger in this process) via mock_logging.py — Phase 7, replacing the
    # process-wide stdout/stderr tee this used to do itself.
    tag = "+".join(f"{model_name}-{kwargs.get('adapter') or 'default'}" for model_name, kwargs in resolved)
    print(f"[mock_service] devices={tag}  state_dir={state_dir}")

    mocks = []
    for model_name, kwargs in resolved:
        mock_cls = _MODEL_REGISTRY[model_name]["cls"]
        try:
            mocks.append(mock_cls(**kwargs))
        except TypeError as e:
            parser.error(f"--device model={model_name}: {e}")
            return  # unreachable — parser.error() exits

    adapter_names = {kwargs["adapter"] for _, kwargs in resolved if kwargs.get("adapter")}

    async def _main_async():
        await _check_adapters_exist(adapter_names)
        await _run_all(mocks)

    log_dir = state_dir / "logs"
    capture_timestamp = time.strftime("%Y-%m-%d_%H-%M")
    btmon_path = log_dir / f"mock-btmon_{capture_timestamp}.btsnoop"
    bluetoothd_path = log_dir / f"mock-bluetoothd-debug_{capture_timestamp}.log"
    btmon_proc = bluetoothd_proc = bluetoothd_log_file = None

    if args.btmon_capture:
        btmon_proc, _ = _start_capture(["sudo", "btmon", "-w", str(btmon_path)], btmon_path)
        print(f"[mock_service] btmon capture: {btmon_path}")

    if args.bluetoothd_debug:
        bluetoothd_proc, bluetoothd_log_file = _start_capture(
            ["sudo", "bluetoothd", "-n", "-d", "--noplugin=battery"],
            bluetoothd_path, tee_to_file=True,
        )
        print(f"[mock_service] bluetoothd debug: {bluetoothd_path}")

    try:
        asyncio.run(_main_async())
    except ValueError as e:
        print(f"[mock_service] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("[mock_service] Interrupted by user. Exiting.")
    finally:
        if btmon_proc is not None:
            _stop_capture("btmon capture", btmon_proc, None, str(btmon_path))
        if bluetoothd_proc is not None:
            _stop_capture("bluetoothd debug", bluetoothd_proc, bluetoothd_log_file, "bluetoothd -n -d")


if __name__ == "__main__":
    main()
