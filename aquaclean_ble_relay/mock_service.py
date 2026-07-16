"""
mock_service.py — single thin CLI entry point for mocking Geberit AquaClean devices.

Phase 4 (docs/developer/mock-service-requirements.md §1/§2/§3): single-device
only. Multi-device concurrency is Phase 5 — passing more than one --device is
rejected here with a clear error rather than silently only starting the first.

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

Logging: opens one auto-named log file per run and tees the process's stdout
to it (console + file), so nobody has to hand-manage a `| tee <name>.log`
filename per test. This is an interim, process-wide redirect appropriate for
Phase 4's single-device scope — it cannot separate concurrent devices' output,
so Phase 5 (multi-device) + Phase 7 (logging polish) need to replace it with
true per-device handlers. MeraMock already opens its own per-adapter log file
independently (Phase 2) — running it through mock_service.py means its output
lands in both files, which is redundant but harmless; cleaning that up is also
Phase 7's job, not this one's.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from aquaclean_ble_relay.mera_mock import MeraMock
from aquaclean_ble_relay.alba_mock import AlbaMock

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
    """Writes to every given stream. See module docstring re: Phase 4 scope."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mock_service.py",
        description="Single thin CLI entry point for mocking Geberit AquaClean devices.",
    )
    parser.add_argument(
        "--device", action="append", dest="devices", type=_parse_device_spec,
        metavar="model=NAME,adapter=HCI[,web_port=PORT][,mode=MODE][,send_delay_sec=SEC]",
        help="One mocked device. Required field: model (one of: %s — see "
             "--list-models for each model's defaults). Every other field is passed "
             "straight through to that model's constructor, overriding the model's "
             "registry defaults — e.g. adapter, web_port (int), mode/send_delay_sec "
             "(Alba only). Phase 4 supports exactly one --device; multi-device is Phase 5."
             % ", ".join(sorted(_MODEL_REGISTRY)),
    )
    parser.add_argument(
        "--state-dir", default=None, metavar="DIR",
        help="Directory for the shared persistence DB and auto-named log files "
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
    if len(args.devices) > 1:
        parser.error(
            f"{len(args.devices)} --device entries given, but Phase 4 only supports "
            "one device — multi-device concurrency is Phase 5 (not yet implemented)"
        )

    spec = dict(args.devices[0])
    model_name = spec.pop("model")
    entry = _MODEL_REGISTRY[model_name]
    mock_cls = entry["cls"]
    kwargs = dict(entry["defaults"])
    kwargs.update(spec)  # explicit --device fields always win over model defaults
    spec = kwargs

    state_dir = Path(args.state_dir) if args.state_dir else Path(__file__).parent / "mock_state"
    spec.setdefault("state_dir", str(state_dir))

    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    adapter_tag = spec.get("adapter") or "default"
    timestamp = time.strftime("%Y-%m-%d_%H-%M")
    log_path = log_dir / f"mock-{model_name}-{adapter_tag}_{timestamp}.log"
    log_file = open(log_path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, log_file)
    sys.stderr = _Tee(sys.stderr, log_file)
    print(f"[mock_service] model={model_name}  log={log_path}")

    try:
        mock = mock_cls(**spec)
    except TypeError as e:
        parser.error(f"--device model={model_name}: {e}")
        return  # unreachable — parser.error() exits

    try:
        asyncio.run(mock.run())
    except KeyboardInterrupt:
        print("[mock_service] Interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
