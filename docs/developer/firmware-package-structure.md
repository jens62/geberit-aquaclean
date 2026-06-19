# Firmware Package Structure

Analysis of Geberit AquaClean firmware packages as downloaded from the Geberit cloud
firmware API (via `tools/geberit-firmware-download.py`).

---

## Package container format (both models)

All firmware files from the cloud are **ZIP archives** with a `.bin` extension.

```
FwPkg_<ID>_<date>_<hash>_<variant>.bin  ← ZIP file, rename to .zip to inspect
├── FirmwarePackage.json                 ← metadata; NodeId list, series, variants
├── FWUpdateInfo.json                    ← deployment contract rules
└── <VariantFolder>/
    └── <firmware node files>
```

### `FirmwarePackage.json` key fields

| Field | Description |
|-------|-------------|
| `series` | Device line (e.g. `248` = Mera Comfort/AcSela, `250` = Alba) |
| `variants` | Integer list of hardware variants the package supports |
| `packageVersion` | Full version string `<RS>.<minor>.<TS>.<date>` |
| `deployContract` | OTA protocol: `AquaCleanV1` or `Ble2V1` |
| `nodeFirmwares[].nodeId` | Node identifier (hex string or integer) |
| `nodeFirmwares[].rsTsVersion` | RS.TS version pair for this node |
| `bootloaderVariant` | `-1` = bootloader NOT included in update package |

---

## Model comparison

| Property | Mera Comfort (series 248) | Alba (series 250) |
|----------|--------------------------|-------------------|
| Package version tested | RS30.0 TS206 | RS03.TS89 |
| Deploy contract | `AquaCleanV1` | `Ble2V1` |
| Node count | 14 nodes (0x00–0x0E) | 1 node (0x01) |
| Node format | Raw ARM Cortex-M flash images | SFUM container (AES-encrypted) |
| Bootloader included | No (`bootloaderVariant: -1`) | No |
| Encryption | None — plaintext binaries | AES-encrypted inside SFUM |
| Arch | ARM Cortex-M (STM32-style vector table) | nRF52 (Nordic Semiconductor) |

---

## Alba — SFUM format

The single firmware node (`AqCS_FA_00_RS_03_TS_89.sfb`, 175,456 bytes) uses the
**Secure Firmware Update Module** format.

```
Offset       Length     Content
0x00–0x03    4 bytes    Magic: 53 46 55 4D  ("SFUM")
0x04–0x05    2 bytes    Version: 0x0001
0x06–0x07    2 bytes    Header descriptor size: 0x0273 (627 bytes)
0x08–0x0B    4 bytes    Timestamp/checksum field: 0x1B696C26
0x0C–0x7F    116 bytes  Header continuation (signature, hash, key slots)
0x80–0x1FF   384 bytes  0xFF padding
0x200–end    175,072 bytes  AES-encrypted payload
```

The payload is encrypted with a key stored only in the nRF52 bootloader on the device.
The Geberit Home app OTA library sends the SFB blob raw over BLE — no decryption
occurs on the app side. The bootloader decrypts and validates before flashing.

---

## Mera Comfort — node architecture

Series 248, variants 1/2/3, file `FwPkg_F801-02-03_V30.0.206.250722_…_Mera_F8_01_RS_30_00_TS_206.bin`.

### Node map

| Node ID | Subsystem | Binary | Processor | Functions | C output |
|---------|-----------|--------|-----------|-----------|---------|
| `0x00` | BLE controller | 159 KB | **8051** (16-bit) | 188 | 5,529 lines |
| `0x01` | Main controller | 206 KB | ARM Cortex-M (STM32) | 836 | 31,182 lines |
| `0x02` | (not present in RS30) | — | — | — | — |
| `0x03` | Odour extraction | 37 KB | ARM Cortex-M | 64 | 1,605 lines |
| `0x04` | Shower unit | 44 KB | ARM Cortex-M | 50 | 1,389 lines |
| `0x05` | Lid lifter | 49 KB | ARM Cortex-M | 48 | 1,368 lines |
| `0x06` | Dryer module | 54 KB | ARM Cortex-M | 78 | 3,022 lines |
| `0x07` | Hot water unit | 57 KB | ARM Cortex-M | 273 | 8,989 lines |
| `0x08` | Seat heating | 43 KB | ARM Cortex-M | 99 | 3,590 lines |
| `0x09` | Control panel | 42 KB | ARM Cortex-M | 77 | 3,049 lines |
| `0x0A` | User/seat detection | 36 KB | ARM Cortex-M | 55 | 1,372 lines |
| `0x0B` | Motion detection | 39 KB | ARM Cortex-M | 66 | 1,908 lines |
| `0x0C` | Orientation light | 43 KB | ARM Cortex-M | 87 | 3,304 lines |
| `0x0D` | Interface module (standard) | 39 KB | ARM Cortex-M | 63 | 1,842 lines |
| `0x0D (FwSap)` | Interface module 12V — SAP 819.758.F0.0 | 146 KB | ARM Cortex-M (**STM32WBxx**) | 739 | 25,460 lines |
| `0x0E` | Dryer unit | 41 KB | ARM Cortex-M | 100 | 2,614 lines |

**Total static analysis output**: ~96,000 lines of C pseudocode across 15 unique binary images.

Static analysis was performed with Ghidra (headless mode):
- Nodes 0x01–0x0E: `ARM:LE:32:Cortex`, BinaryLoader, base address `0x08000000`
- Node 0x00: `8051:BE:16:default` — see architecture section below
- FirmwareWithSap variant: `ARM:LE:32:Cortex`, base `0x08000000`,
  Reset=`0x08029729`, SP=`0x20014F88`

Output files live in `local-assets/firmware/mera_comfort_RS30_TS206_extracted/` and are
excluded from git. The `0x0D FirmwareWithSap` variant uses a completely different MCU
and is nearly 4× larger than standard `0x0D` — see the dedicated section below.

---

## BLE controller node (0x00) — 8051 architecture

Node 0x00 (RS10 TS18, 158,992 bytes) is **not** an ARM binary. Initial static analysis
with `ARM:LE:32:Cortex` produced zero functions — the binary has no ARM Cortex-M vector
table. Inspection of the first bytes revealed the true architecture:

```
Offset 0x0000:  02 1c 8b    ← LJMP 0x1c8b  (8051: 3-byte absolute long jump)
Offset 0x002B:  02 29 c5    ← LJMP 0x29c5
Offset 0x0043:  02 2d f7    ← LJMP 0x2df7
```

The 8051 interrupt vector table starts at `0x0000` with 3-byte `LJMP` instructions at
fixed offsets (`0x0000`, `0x002B`, `0x0043`, `0x0053`, `0x0073`, `0x0083`). Main
application code starts at `0x0090`. After switching to `8051:BE:16:default` and
triggering disassembly from these offsets, Ghidra found **188 functions**.

This is consistent with Nordic Semiconductor's BLE controller architecture: the radio
subsystem often uses a smaller 8-bit processor (8051 or similar) for the link-layer
stack, separate from the ARM application processor. The 8051 core in this node handles
the low-level BLE radio and presents the OTA service GATT table.

### OTA service UUIDs

Node 0x00 contains 9 OTA update GATT service UUIDs stored little-endian in flash.
Base UUID pattern: `9d423433-XXXX-XXXX-XXXX-XXXX3334429d`.

| Offset | UUID (big-endian canonical form) | Notes |
|--------|----------------------------------|-------|
| 0x697a | `90f34c41-a02d-5cb3-a73e-00003334429d` | OTA characteristic 7 |
| 0x698a | `90f34c41-a02d-5cb3-a63e-00003334429d` | OTA characteristic 6 |
| 0x699a | `90f34c41-a02d-5cb3-a53e-00003334429d` | OTA characteristic 5 |
| 0x69aa | `90f34c41-a02d-5cb3-a43e-00003334429d` | OTA characteristic 4 |
| 0x69ba | `90f34c41-a02d-5cb3-a33e-00003334429d` | OTA characteristic 3 |
| 0x69ca | `591bd6b1-da48-6788-d2df-dc253334429d` | Different UUID — purpose unknown |
| 0x6a1a | `90f34c41-a02d-5cb3-a23e-00003334429d` | OTA characteristic 2 |
| 0x6a2a | `90f34c41-a02d-5cb3-a13e-00003334429d` | OTA characteristic 1 |
| 0x6a3a | `0032004b-0064-0096-00fa-01f43334429d` | Different UUID — purpose unknown |

### Strings in node 0x00

```
Geberit
Geberit AC PRO
Geberit AquaClean pro
Characteristic 1  … Characteristic 8
APP ab cdefg
experimentalBLD ab cdefg
```

The product strings confirm this node handles GATT advertisement and the device-name
characteristic. Node 0x00 has no UI language strings.

---

## Why data UUIDs (559eb001/b002) are NOT in the update package

The data communication UUIDs `559eb001` / `559eb002` (write/notify) do not appear
in any of the 14 Mera Comfort node firmware files. `FirmwarePackage.json` confirms
`bootloaderVariant: -1`, meaning the bootloader is not included in the OTA package.

**Conclusion:** the `559eb001`/`b002` UUIDs live in the bootloader firmware, which is
never updated via OTA — it is factory-flashed and persistent. This is consistent with
BLE behavior: the device advertises data services using the bootloader's GATT table
before any application firmware runs, and the same UUIDs remain after an OTA update.

---

## Main controller node (0x01) — ARM binary analysis

Node 0x01 (RS30 TS206, 210,820 bytes = 206 KB) is a raw ARM Cortex-M flash image.
The node covers flash addresses `0x08000000`–`0x08033784`.

### Memory layout

| Field | Value | Notes |
|-------|-------|-------|
| Flash base | `0x08000000` | STM32-style vector table |
| Flash end (this node) | `0x08033784` | |
| Initial stack pointer | `0x20005B48` | 22 KB stack from SRAM base `0x20000000` |
| Function range (auto-discovered) | `0x08004938`–`0x0802f144` | |

### Vector table — named entry points

The ARM Cortex-M vector table at `0x08000000` provides the only named function
addresses in an otherwise fully stripped binary.

| Handler | Address | Notes |
|---------|---------|-------|
| `Reset_Handler` | `0x0803B0FC` | **OUTSIDE this binary** — in another node's flash |
| `NMI_Handler` | `0x0803B10C` | Outside this binary |
| `HardFault_Handler` | `0x0803B10E` | Outside this binary |
| `SysTick_Handler` | `0x08017568` | Inside — `FUN_08017516` +82 bytes |
| `IRQ3` | `0x08017598` | Inside |
| `IRQ6` | `0x080200C8` | Inside |
| `IRQ16` | `0x080279AE` | Inside (large unanalyzed region) |
| `IRQ17` | `0x080279D8` | Inside |
| `IRQ20` | `0x0801D264` | Inside |
| `IRQ28` | `0x080175CC` | Inside |
| `IRQ29` | `0x080208BA` | Inside |
| `IRQ30` | `0x0801E482` | Inside |

**IRQ distribution:** 14 interrupt handlers reside in node 0x01 (RTOS tick, UART,
timers, a handful of GPIO). The remaining **50 IRQ handlers** point to addresses
outside `0x08000000`–`0x08033784` — they belong to the subsystem nodes and the
startup/ISR stubs at `0x0803B*`.

**Why Reset_Handler is outside:** each node is independently OTA-updated, but all
nodes share the same STM32 flash. The startup and default ISR stubs at `0x0803B*` are
owned by a different node (node 0x00 or the bootloader region), never modified by a
node-0x01 OTA update.

### Static analysis (ARM Cortex-M, LE, 32-bit)

- **836 functions** identified, all stripped (`FUN_XXXXXXXX` names only)
- No DWARF debug info; no symbol table
- 4 thunks at `0x0800f564`, `0x08015528`, `0x0801f2a0`, `0x080200d2`/`0x08020b6e`

**Largest functions** (by address-gap heuristic):

| Address | Est. size | Likely role |
|---------|-----------|-------------|
| `FUN_080215a4` | ~50 KB | ARM Thumb-2 machine code — likely main state machine |
| `FUN_0800cd2c` | ~6.3 KB | Candidate: protocol handler / main control loop |
| `FUN_0800ad74` | ~4.8 KB | Candidate: initialization / BLE message handler |
| `FUN_0801beee` | ~4.3 KB | |
| `FUN_080059c4` | ~3.3 KB | |
| `FUN_0802e594` | ~3.0 KB | Startup or ISR stub cluster |
| `FUN_08013248` | ~3.0 KB | |

The 50 KB region at `0x080215a4`–`0x0802da8e` is ARM machine code (16% printable,
consistent with Thumb-2 encoding), not a string table. Ghidra auto-analysis did not
subdivide it — IRQ16/17 fall inside this region.

### SAP numbers — device variant table

All 7 variant SAP numbers are packed contiguously at flash address `0x08009214`,
null-byte separated:

```
0x08009214: 818.618.00.0 | 819.729.00.0 | 832.395.00.0 | 832.394.00.0
            818.978.00.0  812.761.00.0   828.005.00.0
```

The function near `FUN_080091d4` implements variant-detection logic (`strcmp`-based)
that configures the device at startup.

### Multi-language UI strings

The main controller carries the full UI string table for 25+ languages
(EN, DE, FR, IT, ES, PT, NL, DK, NO, SE, FI, PL, CS, SK, SL, HR, SR, RO, HU, TR, …).

Selected strings confirm expected functionality:
```
Profile settings         Profilindstillinger    Profileinstellungen
Water hardness           Vannhardhet            Waterhardheid
Orientation light        Orienteringslys        Orientierungslicht
Descaling completed      Ontkalken voltooid     Entkalkung beendet
WC lid opens/closes      WC-Deckel öffnet       Il cop. WC si apre
Filter ersetzen          Replace filter         Remplacer filtre
Demo mode switched on
Serial number / Article number / Commissioning date
Shower arm offset / Dryer arm offset / WC lid position
```

Node 0x01 owns the entire UI and application logic.

---

## Interface module 12V variant (0x0D FirmwareWithSap) — STM32WBxx

The `FirmwareWithSap` folder in the package contains a second firmware image for
node 0x0D: `0x0D FW Appl Schnittstellenmodul12V AqC GH SAP819.758.F0.0 RS01 TS12.bin`
(149,928 bytes — nearly 4× larger than the standard `0x0D` at 39,784 bytes).

This is a fundamentally different variant of the interface module — a full product
controller with wireless capability, not a thin firmware shim.

### MCU identification — STM32WBxx

The binary contains embedded build-system debug assert paths that reveal the MCU family:

```
C:\_work\FW_Dev\SSM\SSM_12VDC\firmware.5061186_SSM_12VDC\
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_adc.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_cortex.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_dma.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_flash.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_flash_ex.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_gpio.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_rcc.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_rcc_ex.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_rtc.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_smbus.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_tim.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_uart.c
  Drivers\STM32WBxx_HAL_Driver\Src\stm32wbxx_hal_wwdg.c
```

**STM32WBxx** is an ST Microelectronics dual-core SoC: **Cortex-M4** application core
+ **Cortex-M0+** radio processor (BLE 5.2 / Zigbee / Thread). The project name
`SSM_12VDC` matches the node label (Schnittstellenmodul = interface module, 12V DC).
The build machine was a Windows workstation (`C:\_work\FW_Dev\`).

### Key strings

| String | Interpretation |
|--------|---------------|
| `ACBOOT1.0 20210201` | Bootloader version + date (Feb 1, 2021) |
| `PRODUCTCONTROLLER` | RTOS task or service name for the main product controller |
| `SWITCH` | RTOS task or service name for switch/relay control |
| `Tmr Svc` | FreeRTOS timer service thread (canonical FreeRTOS name) |
| `TmrQ` | FreeRTOS timer queue |
| `WBxx-?` / `WBxx-B` / `WBxx-X` / `WBxx-Y` | STM32WB chip variant identifiers |
| `819.758.F0.0` | SAP number of this 12V interface module |
| `828.005.00.0` | SAP number also present — one of the 7 main controller variants |
| `ABCD1234EFGH5` | Test pattern / placeholder serial |

### Why this variant exists

The standard `0x0D` node (39 KB, 63 functions) is a minimal interface module firmware
with strings like `CMD %d`, `FlushFull`, and `LoopBackTest` — a thin protocol adapter.

The FirmwareWithSap variant (146 KB, 739 functions) runs on an STM32WBxx with a full
HAL peripheral driver suite (ADC, DMA, Flash, RTC, SMBUS, TIM, UART, WWDG) and a
FreeRTOS scheduler. It is the **12V DC network interface variant** — a more capable
module that bridges between the Geberit AquaClean internal bus and an external 12V
building network (home automation bus or similar). The STM32WB's built-in BLE/wireless
radio likely enables wireless communication independent of the main BLE controller
(node 0x00).

### Memory layout

| Field | Value |
|-------|-------|
| Flash base | `0x08000000` |
| Binary size | 149,928 bytes (146 KB) |
| Initial stack pointer | `0x20014F88` |
| Reset_Handler | `0x08029729` |
| Functions identified | 739 |

---

## Standard node 0x0D — interface module (for comparison)

Node 0x0D (39,784 bytes = 39 KB, 63 functions). A minimal firmware with no RTOS
and simple protocol handling strings:

```
CMD %d          ← command number format string
FlushFull       ← flush trigger label
LoopBackTest    ← built-in loopback test mode
Dev%x-Rev%x     ← device/revision identifier format
```

This is the interface module for standard Mera Comfort installations without an
external 12V bus. It requires no wireless capability and runs on a simpler MCU.

---

## Relationship to bridge procedures

The firmware node map provides indirect confirmation of the multi-node architecture
that the bridge communicates with via the AquaClean protocol:

| Bridge procedure | Likely node owner |
|-----------------|------------------|
| `GetSystemParameterList` (0x0D) | Node 0x01 (main controller) |
| `GetStoredProfileSettings` (0x53) | Node 0x01 |
| `SetCommand` shower/lid (0x09) | Nodes 0x04, 0x05 (shower unit, lid lifter) |
| `SetCommand` odour extraction (0x09 code 12) | Node 0x03 |
| `SetActiveCommonSetting` orientation light (0x0B) | Node 0x0C |
| Filter status | Node 0x01 (central tracking) |
| `GetDeviceIdentification` (0x82) | Node 0x01 (SAP/serial embedded) |

---

## BLE SMP / LTK and remote-control encryption — negative finding

**Question investigated (2026-06-19):** can the Mera Comfort firmware package help
decode BLE LL-encrypted traffic from the physical remote control?

**Answer: No.** Do not re-investigate this.

### Background

The Mera physical remote (`b0:10:a0:68:5c:8b`, TI OUI) uses BLE SMP bonding.
After `CONNECT_IND` it immediately sends `LL_ENC_REQ` (opcode `0x03`) with
EDIV=`0x0c14`, Rand=`a3 86 b1 bb 54 34 92 3c`.  All subsequent ATT frames are
AES-CCM encrypted.  tshark cannot decode them without the Long Term Key (LTK).

### Why the firmware cannot help

| Layer | Where it lives | Accessible? |
|-------|---------------|-------------|
| BLE SMP stack (key exchange, LTK derivation) | TI CC254x **ROM** — burned at manufacture, not in OTA update | ❌ No |
| LTK storage (EDIV-indexed key lookup) | TI CC254x **NVM** (internal flash) on the toilet | ❌ Physical JTAG only |
| Application firmware (node 0x00, 8051) | OTA-updatable, available in package | ✅ But irrelevant |

Searching all static analysis output across every node for `ltk`, `bond`, `smp`,
`irk`, `ediv`, `pairing`, `encrypt` returns **zero hits**.  This is expected:
the TI CC254x integrates the full BLE stack (including SMP) in on-chip ROM.
The application layer in node 0x00 calls ROM APIs — it does not implement
security primitives itself and none appear in the application binary.

### Second firmware directory

`FwPkg_F801-02-03_V30.0.206.250722_c8ec36cd_Mera_F8_01_RS_30_00_TS_206_extracted/`
is the **same RS30 TS206 package** as `mera_comfort_RS30_TS206_extracted/`.
The size differences (~30 bytes per node) are from annotation differences only —
the new directory has raw Ghidra output; the existing directory has human-renamed
functions added during prior analysis.  There is no new information in the new directory.

### Path forward for remote protocol analysis

The only practical route to decrypted ATT frames from the Mera remote is:
pair the remote with a Linux BlueZ peripheral acting as the toilet.
BlueZ negotiates SMP automatically, stores the LTK in `/var/lib/bluetooth/`,
and `btmon` shows decrypted ATT frames at the kernel level.

See `docs/developer/aquaclean-application-layer-relay.md` § 8.5.

---

## Remote control — series 253 (Fernbedienung)

**Source:** Geberit firmware cloud API — `https://prod.firmwarev1.services.geberit.com/api/firmwares`
(all series returned by a single GET; use `tools/geberit-firmware-download.py --list` to enumerate).

Series 253 (`BOB_FD_01_RS_05_TS_39`) is the **physical remote control** firmware.
"FD" = *Fernbedienung* (German: remote control). "BOB" appears to be Geberit's internal
project name for the remote.

| Property | Value |
|----------|-------|
| Series | 253 |
| Variants | 1, 2, 3, 6, 7, 8, 23 (different remote models/colours) |
| Latest version | RS05.0 TS39 (2026-02-03) |
| Deploy contract | `Ble2V1` (same as Alba) |
| Node count | 1 node (`0x01`) |
| Node format | SFUM container (AES-encrypted) |
| Package filename | `FwPkg_FD01-02-03-06-07-08-17_V5.0.39.260203_abacb9b8_BOB_FD_01_RS_05_TS_39.bin` |

**Key finding — MCU is nRF52, not TI CC254x.**
The OUI `b0:10:a0` (TI) belongs to the BLE radio chip only. The application MCU is nRF52
(confirmed by `Ble2V1` / SFUM deploy contract — same OTA format as the Alba nRF52 device).
The CC254x OUI assumption was wrong; the TI chip is the radio, nRF52 is the host.

**Firmware is unreadable.** The single `.sfb` payload is AES-encrypted (SFUM format).
Decryption requires the bootloader key burned into the nRF52 on the remote — not available.
Static analysis is therefore not possible from the downloaded package.

**Practical implication:** remote control protocol analysis cannot come from firmware inspection.
The only viable route is the BlueZ peripheral pairing + btmon approach (§ 8.5 of
`docs/developer/aquaclean-application-layer-relay.md`).

---

## Related files

| File | Role |
|------|------|
| `local-assets/firmware/FwPkg_FA00_V3.0.89_extracted/` | Alba firmware extracted |
| `local-assets/firmware/mera_comfort_RS30_TS206_extracted/` | Mera Comfort firmware extracted + static analysis output |
| `tools/geberit-firmware-download.py` | Downloads firmware from Geberit cloud |
| `aquaclean_console_app/FirmwareUpdateService.py` | Cloud API client + version parsing |
| `docs/developer/firmware-version.md` | Bridge procedure 0x81 / 0x0E probe results |
| `docs/developer/gatt-uuid-variants.md` | GATT service UUID variants across device models |
