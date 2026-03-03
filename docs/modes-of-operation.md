# Modes of Operation

This table compares the supported ways to integrate the Geberit AquaClean into a home automation setup.
Two independent choices combine: **how the software connects to the toilet** (BLE transport) and **how the software exposes data** (deployment / integration target).

> **openHAB** always uses the standalone bridge via MQTT — see the bottom row.

|  | **Local BLE** (built-in adapter or USB dongle) | **ESPHome Bluetooth proxy** (ESP32) |
|:---|:---|:---|
| **Standalone bridge** | **Pro:**<br>• No additional device between software and toilet<br>• Web application included<br>• REST API included<br>**Con:**<br>• Computer must be physically near the Geberit (BLE range)<br>• If Home Assistant is the goal: an extra machine to maintain<br><br>Tested: ✅ v2.4.40 | **Pro:**<br>• Machine running the bridge does not need to be near the Geberit<br>• Dedicated BLE adapter — more stable connection<br>• Web application included<br>• REST API included<br>**Con:**<br>• One extra (inexpensive) device to set up<br>• If Home Assistant is the goal: an extra machine to maintain<br><br>Tested: ✅ v2.4.40 |
| **Home Assistant integration (HACS)** | **Pro:**<br>• No additional device<br>• Easy setup<br>**Con:**<br>• Raspberry Pi built-in adapter (BCM4345 chip) unreliable — hardware limitation with bleak 2.1.1; USB dongle may work<br>• HA machine must be near the Geberit<br>• No REST API<br>• No web app<br><br>Tested: ⚠️ v2.4.44-pre — fails on RPi with built-in adapter (hardware limitation; see [hacs-integration.md](hacs-integration.md)) | **Pro:**<br>• **Recommended** for Home Assistant users<br>• Easy setup<br>• HA machine does not need to be near the Geberit<br>**Con:**<br>• One extra (inexpensive) device to set up<br><br>Tested: ✅ v2.4.40 |
| **Home Assistant via MQTT** (standalone bridge + HA MQTT Discovery) | **Pro:**<br>• TBC<br>**Con:**<br>• TBC<br><br>Tested: ✅ v2.4.40 | **Pro:**<br>• HA machine does not need to be near the Geberit<br>• Web application included<br>• REST API included<br>**Con:**<br>• TBC<br><br>Tested: ✅ v2.4.40 |
| **openHAB** | *openHAB always integrates via the standalone bridge (MQTT). The bridge handles the BLE connection and can use a built-in adapter, a USB dongle, or an ESPHome ESP32 proxy — see the Standalone rows above for the BLE transport trade-offs.* | ← same |
