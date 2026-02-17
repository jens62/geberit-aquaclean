# ESPHome Bluetooth Proxy

## What this enables

Normally the machine running the Python bridge must be **physically close to the toilet** because BLE has a limited range of ~10 m.

With an **ESPHome Bluetooth Proxy**, a small ESP32 board sits next to the toilet and acts as a BLE-to-IP bridge.  The Python bridge runs on **any machine on your network** — a server, NAS, or Raspberry Pi elsewhere in the house — and communicates with the ESP32 over Ethernet/IP.

```
AquaClean (BLE) ←→ ESP32-POE-ISO          ←→ [Ethernet / IP] ←→ Python bridge (anywhere)
                   [ESPHome BLE Proxy]           port 6053
                   [powered by PoE]
```

The ESP32 is powered directly by the Ethernet cable via **PoE** (Power over Ethernet) — no separate power supply or USB cable needed.

---

## Hardware

**Recommended board:** [Olimex ESP32-POE-ISO](https://www.olimex.com/Products/IoT/ESP32/ESP32-POE-ISO/) (16 MB flash variant)

- Wired Ethernet with PoE (IEEE 802.3af) — stable, no WiFi interference
- Built-in BLE
- Galvanic isolation on the PoE input (safe in bathrooms)
- Small form factor — easy to hide near the toilet

> The **ESP32-POE-ISO-EA** variant has an external antenna connector for better BLE range.

---

## Flash the ESP32

### 1. Install ESPHome

```bash
pip install esphome
```

### 2. Set your API and OTA passwords

Edit `esphome/secrets.yaml`:

```yaml
api_password: "your-api-password"
ota_password: "your-ota-password"
```

### 3. Flash

Connect the ESP32-POE-ISO via USB, then:

```bash
esphome run esphome/aquaclean-proxy.yaml
```

After the first flash, subsequent updates can be done over the network (OTA) — no USB needed.

### 4. Verify

Open `http://<esp32-ip>/` in a browser.  The ESPHome web interface shows the device status, BLE scan activity, and logs.

---

## Configure the Python bridge

Enable the proxy in `config.ini` by uncommenting and filling in the `[ESPHOME]` section:

```ini
[ESPHOME]
host = 192.168.0.xxx   # IP address of the ESP32-POE-ISO
port = 6053             # ESPHome native API port (default: 6053)
password = your-api-password   # matches api_password in secrets.yaml
```

When `host` is set the bridge automatically routes all BLE traffic through the ESP32.  When `host` is absent or empty the local Bluetooth adapter is used as before — no other changes required.

---

## How it works internally

The Python bridge uses [bleak-esphome](https://github.com/Bluetooth-Devices/bleak-esphome), a drop-in backend for the `bleak` BLE library that routes traffic over the **ESPHome native API** (TCP port 6053) instead of the local Bluetooth adapter.

When the ESPHome proxy is enabled:

1. The bridge scans for the AquaClean's MAC address through the ESP32's BLE scanner
2. Once found, a GATT connection is established through the proxy
3. All subsequent reads, writes, and notifications travel over IP to the ESP32, which relays them to the AquaClean over BLE
4. The connection is released after each request (on-demand mode) or held open (persistent mode)

Install the extra dependency:

```bash
pip install bleak-esphome
```

---

## On-demand mode recommendation

On-demand BLE mode works especially well with the ESPHome proxy.  Each request connects, queries, and disconnects — keeping the BLE slot on the ESP32 free between requests and coexisting cleanly with the Geberit Home app.

See [on-demand-ble.md](on-demand-ble.md) for details.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Bridge cannot find the device | Verify the ESP32 web UI shows BLE scan results; check the AquaClean is powered on |
| Connection timeout | Increase BLE proximity — move the ESP32 closer to the toilet |
| `Connection refused` on port 6053 | Confirm the ESP32 is reachable (`ping <ip>`) and the API is enabled in the YAML |
| Wrong password error | Check `password` in `config.ini` matches `api_password` in `secrets.yaml` |
| OTA update fails | Ensure the ESP32 and the machine running ESPHome are on the same network segment |
