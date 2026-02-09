#!/usr/bin/env python3
"""
Publish Home Assistant MQTT Discovery messages for Geberit AquaClean.

This script publishes discovery messages to MQTT so Home Assistant can
automatically create entities without manual configuration.yaml editing.

Usage:
    # Publish discovery messages (creates entities in Home Assistant)
    python publish_discovery.py --host BROKER_IP
    python publish_discovery.py --host 192.168.1.100 --username user --password pass

    # Remove entities from Home Assistant (publishes empty payloads to MQTT)
    # This removes the entities from Home Assistant's entity registry, not from MQTT
    # The AquaClean device continues to publish to MQTT topics as before
    python publish_discovery.py --host BROKER_IP --remove
"""

import argparse
import json
import sys

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt is required. Install it with: pip install paho-mqtt")
    sys.exit(1)


# Common device information for all entities
DEVICE_INFO = {
    "identifiers": ["geberit_aquaclean"],
    "name": "Geberit AquaClean",
    "model": "Mera Comfort",
    "manufacturer": "Geberit"
}

# Discovery prefix (default in Home Assistant)
DISCOVERY_PREFIX = "homeassistant"


def get_discovery_configs():
    """Return all discovery configurations."""

    configs = []

    # Binary Sensors (4)
    binary_sensors = [
        {
            "topic": f"{DISCOVERY_PREFIX}/binary_sensor/geberit_aquaclean/user_sitting/config",
            "payload": {
                "name": "User Sitting",
                "unique_id": "geberit_aquaclean_user_sitting",
                "state_topic": "Geberit/AquaClean/peripheralDevice/monitor/isUserSitting",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:seat",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/binary_sensor/geberit_aquaclean/anal_shower_running/config",
            "payload": {
                "name": "Anal Shower Running",
                "unique_id": "geberit_aquaclean_anal_shower_running",
                "state_topic": "Geberit/AquaClean/peripheralDevice/monitor/isAnalShowerRunning",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:shower",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/binary_sensor/geberit_aquaclean/lady_shower_running/config",
            "payload": {
                "name": "Lady Shower Running",
                "unique_id": "geberit_aquaclean_lady_shower_running",
                "state_topic": "Geberit/AquaClean/peripheralDevice/monitor/isLadyShowerRunning",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:shower",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/binary_sensor/geberit_aquaclean/dryer_running/config",
            "payload": {
                "name": "Dryer Running",
                "unique_id": "geberit_aquaclean_dryer_running",
                "state_topic": "Geberit/AquaClean/peripheralDevice/monitor/isDryerRunning",
                "payload_on": "True",
                "payload_off": "False",
                "icon": "mdi:air-filter",
                "device": DEVICE_INFO
            }
        }
    ]

    # Sensors (7)
    sensors = [
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/sap_number/config",
            "payload": {
                "name": "SAP Number",
                "unique_id": "geberit_aquaclean_sap_number",
                "state_topic": "Geberit/AquaClean/peripheralDevice/information/Identification/SapNumber",
                "icon": "mdi:identifier",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/production_date/config",
            "payload": {
                "name": "Production Date",
                "unique_id": "geberit_aquaclean_production_date",
                "state_topic": "Geberit/AquaClean/peripheralDevice/information/Identification/ProductionDate",
                "icon": "mdi:calendar",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/serial_number/config",
            "payload": {
                "name": "Serial Number",
                "unique_id": "geberit_aquaclean_serial_number",
                "state_topic": "Geberit/AquaClean/peripheralDevice/information/Identification/SerialNumber",
                "icon": "mdi:barcode",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/description/config",
            "payload": {
                "name": "Description",
                "unique_id": "geberit_aquaclean_description",
                "state_topic": "Geberit/AquaClean/peripheralDevice/information/Identification/Description",
                "icon": "mdi:information",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/initial_operation_date/config",
            "payload": {
                "name": "Initial Operation Date",
                "unique_id": "geberit_aquaclean_initial_operation_date",
                "state_topic": "Geberit/AquaClean/peripheralDevice/information/initialOperationDate",
                "icon": "mdi:calendar-clock",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/connected/config",
            "payload": {
                "name": "Connected",
                "unique_id": "geberit_aquaclean_connected",
                "state_topic": "Geberit/AquaClean/centralDevice/connected",
                "icon": "mdi:bluetooth-connect",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/sensor/geberit_aquaclean/error/config",
            "payload": {
                "name": "Error",
                "unique_id": "geberit_aquaclean_error",
                "state_topic": "Geberit/AquaClean/centralDevice/error",
                "icon": "mdi:alert-circle",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO
            }
        }
    ]

    # Switch (1)
    switches = [
        {
            "topic": f"{DISCOVERY_PREFIX}/switch/geberit_aquaclean/toggle_lid/config",
            "payload": {
                "name": "Toggle Lid",
                "unique_id": "geberit_aquaclean_toggle_lid",
                "command_topic": "Geberit/AquaClean/peripheralDevice/control/toggleLidPosition",
                "payload_on": "true",
                "payload_off": "false",
                "icon": "mdi:toilet",
                "optimistic": True,
                "retain": False,
                "device": DEVICE_INFO
            }
        }
    ]

    return binary_sensors + sensors + switches


def publish_discovery(client, remove=False):
    """
    Publish or remove discovery messages to/from MQTT.

    When remove=True, publishes empty payloads ONLY to Geberit AquaClean
    discovery topics (homeassistant/.../geberit_aquaclean/.../config).
    This only affects the 12 AquaClean entities - all other MQTT entities
    (from other devices or manual configuration.yaml) remain untouched.

    This does NOT stop the AquaClean device from publishing to MQTT -
    it only removes the entities from HA's UI.
    """

    configs = get_discovery_configs()

    print(f"{'Removing' if remove else 'Publishing'} {len(configs)} discovery messages...")

    for config in configs:
        topic = config["topic"]

        if remove:
            # Publish empty payload to remove entity from Home Assistant
            # This tells HA to unregister the entity from its entity registry
            payload = None
            result = client.publish(topic, payload=payload, retain=True)
        else:
            # Publish discovery config
            payload = json.dumps(config["payload"])
            result = client.publish(topic, payload=payload, retain=True)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            entity_name = config["payload"].get("name", "Unknown") if not remove else topic.split("/")[-2]
            print(f"  ✓ {entity_name}")
        else:
            print(f"  ✗ Failed to publish to {topic}")

    print(f"\n{'Removal' if remove else 'Discovery'} complete!")
    if not remove:
        print("\nCheck Home Assistant:")
        print("  Settings → Devices & Services → MQTT → Geberit AquaClean")


def main():
    parser = argparse.ArgumentParser(
        description="Publish Home Assistant MQTT Discovery messages for Geberit AquaClean"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="MQTT broker hostname/IP (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="MQTT broker port (default: 1883)"
    )
    parser.add_argument(
        "--username",
        help="MQTT username (optional)"
    )
    parser.add_argument(
        "--password",
        help="MQTT password (optional)"
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove ONLY Geberit AquaClean entities from Home Assistant by publishing empty "
             "discovery payloads to their specific MQTT topics. This only affects the 12 "
             "AquaClean entities created by this script. Other MQTT entities (from other devices "
             "or manual configuration.yaml entries) are NOT affected. Safe to use."
    )

    args = parser.parse_args()

    # Create MQTT client with callback API version to avoid deprecation warning
    # paho-mqtt 2.0+ requires specifying the callback API version
    # Using VERSION2 (latest) since this script doesn't use callbacks
    try:
        # Try new API (paho-mqtt >= 2.0)
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        # Fallback for older paho-mqtt versions
        client = mqtt.Client()

    # Set credentials if provided
    if args.username:
        client.username_pw_set(args.username, args.password)

    # Connect to broker
    print(f"Connecting to MQTT broker at {args.host}:{args.port}...")
    try:
        client.connect(args.host, args.port, 60)
    except Exception as e:
        print(f"Error connecting to MQTT broker: {e}")
        sys.exit(1)

    print("Connected!\n")

    # Publish discovery messages
    publish_discovery(client, remove=args.remove)

    # Disconnect
    client.disconnect()


if __name__ == "__main__":
    main()
