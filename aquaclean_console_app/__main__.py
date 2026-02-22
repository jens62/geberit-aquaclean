"""Entry point for `python -m aquaclean_console_app`."""
import argparse
import os
import sys

from aiorun import run

from aquaclean_console_app.main import main, JsonArgumentParser


def entry_point():
    parser = JsonArgumentParser(
        prog="aquaclean-bridge",
        description="Geberit AquaClean Controller",
        epilog=(
            "device state queries (require BLE):\n"
            "  %(prog)s --mode cli --command status\n"
            "  %(prog)s --mode cli --command system-parameters\n"
            "  %(prog)s --mode cli --command user-sitting-state\n"
            "  %(prog)s --mode cli --command anal-shower-state\n"
            "  %(prog)s --mode cli --command lady-shower-state\n"
            "  %(prog)s --mode cli --command dryer-state\n"
            "\n"
            "device info queries (require BLE):\n"
            "  %(prog)s --mode cli --command info\n"
            "  %(prog)s --mode cli --command identification\n"
            "  %(prog)s --mode cli --command initial-operation-date\n"
            "  %(prog)s --mode cli --command soc-versions\n"
            "\n"
            "device commands (require BLE):\n"
            "  %(prog)s --mode cli --command toggle-lid\n"
            "  %(prog)s --mode cli --command toggle-anal\n"
            "\n"
            "app config / home assistant (no BLE required):\n"
            "  %(prog)s --mode cli --command check-config\n"
            "  %(prog)s --mode cli --command get-config\n"
            "  %(prog)s --mode cli --command publish-ha-discovery\n"
            "  %(prog)s --mode cli --command remove-ha-discovery\n"
            "\n"
            "ESPHome proxy (no BLE required):\n"
            "  %(prog)s --mode cli --command esp32-connect\n"
            "  %(prog)s --mode cli --command esp32-disconnect\n"
            "\n"
            "options:\n"
            "  --address 38:AB:XX:XX:ZZ:67   override BLE device address from config.ini\n"
            "\n"
            "CLI results and errors are written to stdout as JSON.\n"
            "Log output goes to stderr (redirect with 2>logfile)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--mode', choices=['service', 'cli', 'api'], default='service')
    parser.add_argument('--command', choices=[
        # device state queries
        'status', 'system-parameters',
        'user-sitting-state', 'anal-shower-state', 'lady-shower-state', 'dryer-state',
        # device info queries
        'info', 'identification', 'initial-operation-date', 'soc-versions',
        # device commands
        'toggle-lid', 'toggle-anal',
        # app config / home assistant (no BLE required)
        'check-config', 'get-config', 'publish-ha-discovery', 'remove-ha-discovery',
        # ESPHome proxy (no BLE required)
        'esp32-connect', 'esp32-disconnect',
    ])
    parser.add_argument('--address')

    args = parser.parse_args()
    run(main(args))


if __name__ == "__main__":
    entry_point()
