"""Entry point for `python -m aquaclean_console_app`."""
import argparse
import os
import sys

from aiorun import run

from aquaclean_console_app.main import main, JsonArgumentParser, _bridge_version


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
            "  %(prog)s --mode cli --command statistics-descale\n"
            "  %(prog)s --mode cli --command filter-status\n"
            "  %(prog)s --mode cli --command firmware-version-list\n"
            "\n"
            "device commands (require BLE):\n"
            "  %(prog)s --mode cli --command toggle-lid\n"
            "  %(prog)s --mode cli --command toggle-anal\n"
            "  %(prog)s --mode cli --command toggle-lady\n"
            "  %(prog)s --mode cli --command toggle-dryer\n"
            "  %(prog)s --mode cli --command toggle-orientation-light\n"
            "  %(prog)s --mode cli --command reset-filter-counter\n"
            "  %(prog)s --mode cli --command trigger-flush-manually\n"
            "  %(prog)s --mode cli --command prepare-descaling\n"
            "  %(prog)s --mode cli --command confirm-descaling\n"
            "  %(prog)s --mode cli --command cancel-descaling\n"
            "  %(prog)s --mode cli --command postpone-descaling\n"
            "  %(prog)s --mode cli --command start-cleaning-device\n"
            "  %(prog)s --mode cli --command execute-next-cleaning-step\n"
            "  %(prog)s --mode cli --command start-lid-calibration\n"
            "  %(prog)s --mode cli --command lid-offset-save\n"
            "  %(prog)s --mode cli --command lid-offset-increment\n"
            "  %(prog)s --mode cli --command lid-offset-decrement\n"
            "\n"
            "app config / home assistant (no BLE required):\n"
            "  %(prog)s --mode cli --command check-config\n"
            "  %(prog)s --mode cli --command get-config\n"
            "  %(prog)s --mode cli --command publish-ha-discovery\n"
            "  %(prog)s --mode cli --command remove-ha-discovery\n"
            "  %(prog)s --mode cli --command system-info\n"
            "  %(prog)s --mode cli --command performance-stats\n"
            "  %(prog)s --mode cli --command performance-stats --format markdown\n"
            "\n"
            "ESPHome proxy (no BLE required):\n"
            "  %(prog)s --mode cli --command esp32-connect\n"
            "  %(prog)s --mode cli --command esp32-disconnect\n"
            "\n"
            "options:\n"
            "  --address 38:AB:XX:XX:ZZ:67   override BLE device address from config.ini\n"
            "  --format json|markdown         output format for performance-stats (default: json)\n"
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
        'info', 'identification', 'initial-operation-date', 'soc-versions', 'node-list',
        'statistics-descale', 'filter-status', 'firmware-version-list', 'profile-settings',
        # device commands
        'toggle-lid', 'toggle-anal', 'toggle-lady', 'toggle-dryer', 'toggle-orientation-light',
        'reset-filter-counter', 'trigger-flush-manually',
        'prepare-descaling', 'confirm-descaling', 'cancel-descaling', 'postpone-descaling',
        'start-cleaning-device', 'execute-next-cleaning-step',
        'start-lid-calibration', 'lid-offset-save', 'lid-offset-increment', 'lid-offset-decrement',
        # app config / home assistant (no BLE required)
        'check-config', 'get-config', 'publish-ha-discovery', 'remove-ha-discovery',
        # system info + performance stats (no BLE required)
        'system-info', 'performance-stats',
        # ESPHome proxy (no BLE required)
        'esp32-connect', 'esp32-disconnect',
    ])
    parser.add_argument('--address')
    parser.add_argument('--format', choices=['json', 'markdown'], default='json',
                        help='Output format for performance-stats (default: json)')
    parser.add_argument('--ha-discovery', default=None,
                        action=argparse.BooleanOptionalAction,
                        dest='ha_discovery',
                        help='Publish HA MQTT discovery on startup (overrides config ha_discovery_on_startup)')
    parser.add_argument('--version', action='version',
                        version=f'aquaclean-bridge {_bridge_version}')

    args = parser.parse_args()
    run(main(args))


if __name__ == "__main__":
    entry_point()
