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

### 2. Set your OTA password (and optionally an API encryption key)

Edit `esphome/secrets.yaml`:

```yaml
ota_password: "your-ota-password"

# Optional: enable API encryption (ESPHome 2026.1+).
# Generate a key with:  openssl rand -base64 32
# Then uncomment the api: encryption: block in the proxy YAML.
# api_encryption_key: "your-base64-key"
```

The API is open (no authentication) by default — acceptable on a trusted home LAN.  To add encryption, generate a key, put it in both `secrets.yaml` and `config.ini` (`noise_psk`), and uncomment the `api: encryption:` block in the proxy YAML.

> **ESPHome 2026.1 note:** `api: password:` was removed.  Use `api: encryption: key:` for auth, or just bare `api:` for no auth.  The encryption key **must** be valid base64 — `openssl rand -base64 32` generates one.  Placeholder strings like `"change-me"` will fail validation.

### 3. Flash

Two config variants are provided — pick the one matching your network setup:

| File | Network | When to use |
|------|---------|-------------|
| `esphome/aquaclean-proxy-wifi.yaml` | WiFi (2.4 GHz) | Quick start; no extra hardware needed |
| `esphome/aquaclean-proxy-eth.yaml` | Wired Ethernet + PoE | Recommended for permanent installation |

Set WiFi credentials in `esphome/secrets.yaml` (`wifi_ssid` / `wifi_password`) before flashing the WiFi variant.

Connect the ESP32-POE-ISO via USB, then:

```bash
# WiFi variant
esphome run esphome/aquaclean-proxy-wifi.yaml

# Ethernet/PoE variant
esphome run esphome/aquaclean-proxy-eth.yaml
```

After the first flash, subsequent updates can be done over the network (OTA) — no USB needed.

### 4. Verify

Open `http://<esp32-ip>/` in a browser.  The ESPHome web interface shows the device status, BLE scan activity, and logs.

Before connecting, quickly verify port 6053 is reachable (macOS and Linux):

```bash
nc -zw1 192.168.0.160 6053 && echo open || echo closed
```

Stream the live log directly from the command line:

```bash
esphome logs esphome/aquaclean-proxy-wifi.yaml --device 192.168.0.160
# or for the Ethernet variant:
esphome logs esphome/aquaclean-proxy-eth.yaml --device 192.168.0.160
```

BLE advertisements appear in the log as the ESP32 hears them — useful to confirm scanning is working before running the Python bridge.

> **Log level and BLE visibility:** the proxy YAMLs use `logger: level: INFO` by default.  BLE scan events are logged at DEBUG/VERBOSE level and will **not** appear at INFO.  If you expect to see BLE activity in the log but the output looks quiet after startup, that is normal — not a fault.
>
> To see BLE events temporarily, change the log level to DEBUG in the YAML and OTA reflash:
> ```yaml
> logger:
>   level: DEBUG
> ```
> Change back to INFO for permanent use — DEBUG is very chatty at runtime.

> **Note:** `esphome logs` validates the YAML locally before connecting to the device.  The config must compile clean even for log streaming — no need to flash first, but all YAML errors must be resolved.

---

## Find the ESP32 IP address

After first boot the ESP32 gets an IP from your DHCP server.  Scan your network for any host with port 6053 open (the ESPHome native API port):

```bash
sudo nmap -p6053 -sS -Pn $(ip -o -4 addr show scope global | awk '{print $4}' | head -n1) -oX - | \
xmlstarlet sel -t \
  -m "//host[ports/port/state[@state='open']]" \
  -v "address[@addrtype='ipv4']/@addr" -o ',' \
  -v "address[@addrtype='mac']/@addr" -o ',' \
  -v "hostnames/hostname/@name" -o ',' \
  -v "ports/port[@portid='6053']/state/@state" -o ',' \
  -v "address[@addrtype='mac']/@vendor" -n
```

Example output:

```
192.168.0.160,78:42:1C:38:DE:14,aquaclean-proxy.fritz.box,open,
```

The fields are `ip,mac,hostname,state,vendor`.  The vendor field is populated from nmap's MAC OUI database — it may be empty if the OUI is not in nmap's list, but the open port 6053 and the mDNS hostname are the reliable identifiers.  Requires `nmap` and `xmlstarlet`.

> **Make the address stable.**  Once you know the IP, either:
> - assign a **DHCP reservation** (static lease) in your router by MAC address, or
> - use the **hostname** if your network resolves it: ESPHome publishes `aquaclean-proxy.local` (the `name:` field in the YAML).  Fritz!Box users can use `aquaclean-proxy.fritz.box` directly.  Use the hostname in `config.ini` instead of a raw IP:
>
> ```ini
> host = aquaclean-proxy.fritz.box   # Fritz!Box
> host = aquaclean-proxy.local       # generic mDNS (Linux/macOS; Windows needs Bonjour)
> ```

---

## First test — BLE scanner

Before connecting the full bridge, verify the ESP32-POE-ISO can see your AquaClean.  `esphome/ble-scan.py` scans via the proxy and prints every BLE device in range as a table:

```bash
python esphome/ble-scan.py aquaclean-proxy.fritz.box
python esphome/ble-scan.py aquaclean-proxy.fritz.box --duration 20 --noise-psk "base64key=="
```

Example output:

```
Scanning via aquaclean-proxy.fritz.box:6053 for 10 s …

MAC Address          RSSI       Name
----------------------------------------------------------
38:AB:XX:XX:ZZ:67    -62 dBm   Geberit AC PRO
...

3 device(s) found.
```

**Device names:** the ESP32-POE-ISO uses active scanning (`esp32_ble_tracker.scan_parameters.active: true` in both proxy YAMLs).  With active scanning the ESP32 sends a scan request after each advertisement and receives the scan response packet, which typically contains the device name.  If a device only advertises a short name or none at all, the name column will be empty.

---

## Configure the Python bridge

Enable the proxy in `config.ini` by uncommenting and filling in the `[ESPHOME]` section:

```ini
[ESPHOME]
host = 192.168.0.xxx        # IP address of the ESP32-POE-ISO
port = 6053                  # ESPHome native API port (default: 6053)
noise_psk = base64key==      # matches api_encryption_key in secrets.yaml
```

When `host` is set the bridge automatically routes all BLE traffic through the ESP32.  When `host` is absent or empty the local Bluetooth adapter is used as before — no other changes required.

---

## Log Streaming (Optional)

The Python bridge can stream live logs from the ESP32 device and integrate them into the console app logging — useful for debugging proxy issues, BLE scanner problems, or correlation between ESP32 events and app behavior.

**When to enable:**
- Troubleshooting BLE connection issues
- Debugging ESP32 proxy behavior
- Diagnosing WiFi/network problems on the ESP32
- Development and testing

**When to disable (default):**
- Production use — very verbose, generates constant log traffic
- Normal operation — ESP32 logs system events unrelated to the AquaClean

### Configuration

Add to the `[ESPHOME]` section in `config.ini`:

```ini
[ESPHOME]
host = 192.168.0.xxx
port = 6053
noise_psk = base64key==
log_streaming = false      # Enable log streaming (true/false)
log_level = INFO           # Log level: ERROR | WARN | INFO | DEBUG | VERBOSE
```

**Log levels:**

| Level | What you see |
|-------|--------------|
| `ERROR` | Errors only |
| `WARN` | Warnings and errors |
| `INFO` | Normal operations (recommended for debugging) |
| `DEBUG` | Detailed BLE operations, scanner activity |
| `VERBOSE` | Everything — very chatty |

### Output Format

ESP32 logs are prefixed with `[ESP32:tag]` where `tag` is the ESPHome component (e.g., `bluetooth_proxy`, `wifi`, `esp32_ble_tracker`):

```
2026-02-19 10:30:15 INFO: ESPHome log streaming enabled (level=INFO)
2026-02-19 10:30:16 INFO: [ESP32:bluetooth_proxy] Connecting v3 without cache
2026-02-19 10:30:16 DEBUG: [ESP32:esp32_ble_tracker] Setting coexistence to Bluetooth
2026-02-19 10:30:16 INFO: [ESP32:esp32_ble_client] Connection open (MTU: 517)
2026-02-19 10:30:17 WARNING: [ESP32:wifi] WiFi signal weak: -78 dBm
```

**Performance note:** Log streaming uses a persistent API connection separate from BLE operations.  At `INFO` level the overhead is minimal.  At `DEBUG` or `VERBOSE` the ESP32 can log hundreds of messages per minute — keep disabled unless actively debugging.

---

## How it works internally

The Python bridge uses [aioesphomeapi](https://github.com/esphome/aioesphomeapi), the official ESPHome API client library, to communicate directly with the ESP32 over the **ESPHome native API** (TCP port 6053).  A bleak-compatible wrapper (`ESPHomeAPIClient`) translates between bleak's UUID-based interface and aioesphomeapi's handle-based protocol.

When the ESPHome proxy is enabled:

1. The bridge connects to the ESP32 via `aioesphomeapi.APIClient`
2. It scans for the AquaClean's MAC address through the ESP32's raw BLE advertisements
3. Once found, a GATT connection is established through the proxy
4. GATT services are enumerated and a UUID↔handle mapping is built
5. All subsequent reads, writes, and notifications travel over IP to the ESP32, which relays them to the AquaClean over BLE
6. The connection is released after each request (on-demand mode) or held open (persistent mode)

**Why aioesphomeapi instead of bleak-esphome?**
The `bleak-esphome` library v3.x requires Home Assistant's `habluetooth` infrastructure, which is not available in standalone applications.  Using `aioesphomeapi` directly removes this dependency and keeps the bridge fully standalone.

Install the extra dependency:

```bash
pip install aioesphomeapi
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
| Encryption / auth error | Check `noise_psk` in `config.ini` matches `api_encryption_key` in `secrets.yaml` |
| OTA update fails | Ensure the ESP32 and the machine running ESPHome are on the same network segment |

For detailed troubleshooting — ESPHome 2026.1 breaking changes, macOS mDNS conflicts, bleak-esphome API changes, and more — see [esphome-troubleshooting.md](esphome-troubleshooting.md).
