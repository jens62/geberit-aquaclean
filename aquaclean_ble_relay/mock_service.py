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

Multiple devices CAN share one adapter — BlueZ supports multiple GATT
applications and multiple advertisement instances per adapter (confirmed:
this VM's adapter reports SupportedInstances=3). What's rejected is two
--device entries with the exact same (model, adapter) pair, since that would
have both instances registering under identical D-Bus object paths. Two
mock/protocol-level fixes were needed to make sharing an adapter actually
safe, both applied directly in mera_mock.py/alba_mock.py, not here:
  1. D-Bus GATT app paths are now prefixed by model name AND adapter — they
     used to be tagged by adapter only, so Mera's and Alba's generically-named
     "battery"/"dis" service paths would collide if they ever shared an
     adapter.
  2. MeraMock's _emit_interface_added suppression (needed to work around a
     bluez_peripheral/BlueZ characteristic-registration race) used to patch
     dbus_next.message_bus.BaseMessageBus at the CLASS level — process-wide,
     so two concurrent registrations would race each other's patch/restore.
     Now scoped to each instance's own `bus` object.

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

Logging: opens one auto-named log file per run and tees the process's stdout
to it (console + file), so nobody has to hand-manage a `| tee <name>.log`
filename per test. This is still a process-wide stdout/stderr redirect — it
cannot separate concurrent devices' interleaved output into distinct files,
so for 2+ devices the log filename reflects the whole batch (not one device)
and everyone's lines land in the one combined file. True per-device log
files/handlers are Phase 7's job. MeraMock already opens its own per-adapter
log file independently (Phase 2) — running it through mock_service.py means
its output lands in both files, which is redundant but harmless.
"""

import argparse
import asyncio
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


class _Tee:
    """Writes to every given stream. See module docstring re: logging scope.

    Delegates any attribute it doesn't itself define (isatty, fileno,
    encoding, ...) to the first stream — libraries that receive this as
    sys.stdout (uvicorn's logging setup calls .isatty()) expect a real
    file-like object, not just something with write()/flush().
    """

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()

    def isatty(self):
        return self._streams[0].isatty()

    def __getattr__(self, name):
        return getattr(self._streams[0], name)


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

    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d_%H-%M")
    tag = "+".join(f"{model_name}-{kwargs.get('adapter') or 'default'}" for model_name, kwargs in resolved)
    log_path = log_dir / f"mock-{tag}_{timestamp}.log"
    log_file = open(log_path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)
    print(f"[mock_service] devices={tag}  log={log_path}")

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

    try:
        asyncio.run(_main_async())
    except ValueError as e:
        print(f"[mock_service] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("[mock_service] Interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
