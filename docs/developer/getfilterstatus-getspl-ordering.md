# GetFilterStatus / GetSystemParameterList investigation

**Status: production workaround validated — 2026-08-17**

## Scope

This document describes a reproducible behavior on the tested **Geberit AquaClean Mera**
running **RS30.0 TS206**, using the bridge's current BLE transport (ESPHome proxy, ATT MTU 23).

Do **not** assume that every AquaClean model or firmware revision behaves identically.
A newer nRF52840 capture from another Mera firmware shows the iOS app using a larger
GetSystemParameterList request without the same observed failure.

## Final finding

`GetSystemParameterList` (proc `0x0D`) can complete successfully while still leaving the
toilet in an internal state where subsequent `GetFilterStatus` (proc `0x59`) requests receive
no data response.

On RS30.0 TS206 the bridge can reproduce the failure when **meaningful GetSPL request bytes
extend past the first transport frame into the continuation frame**.

The important distinction is not the semantic identity of SPL parameters 12/13:

- `GetSPL [12]` — safe
- `GetSPL [13]` — safe
- `GetSPL [12,13]` — safe
- `GetSPL [0,1,2,3,4,5,6,7]` followed by `GetSPL [12,13]` — safe
- `GetSPL [0,1,2,3,4,5,6,7,12,13]` in one request — GetSPL succeeds, then 0x59 becomes unresponsive

The tested production workaround is therefore:

```text
GetSPL [0,1,2,3,4,5,6,7]
GetSPL [12,13]
```

Both persistent and on-demand polling use this split.

## Why the combined request is especially deceptive

The failing combined request is **not rejected**.

The toilet:

1. accepts WRITE_0/FIRST;
2. accepts WRITE_1/CONS;
3. acknowledges the transport frames;
4. returns a valid GetSPL result containing the requested values;
5. only afterwards stops answering GetFilterStatus.

In the M-series A/B test the failing combined request returned all ten values, including the
two values requested in the continuation part. The continuation frame was therefore not
simply lost or ignored.

## Diagnostic evidence

| Test | GetSPL request / variation | Result for subsequent 0x59 |
|---|---|---|
| H1 | 8 real params `[0,1,2,3,4,5,6,4]` | PASS |
| H2A/H2B | 9 params, ninth real byte `0x00` | PASS |
| H3A | ninth real byte `0x04` in CONS | FAIL |
| J1 | ninth real byte `0x08` in CONS | FAIL |
| L1 | same known-failing request with 30 ms FIRST→CONS delay | FAIL |
| M1 | `[12]` | PASS |
| M2 | `[13]` | PASS |
| M3 | `[12,13]` | PASS |
| M4 | `[0..7]` then `[12,13]` | PASS |
| M5 | `[0..7,12,13]` combined | FAIL |

Additional findings:

- a 10-second RPC-free wait after GetSPL does not recover 0x59;
- BLE disconnect/reconnect does not recover it;
- a fresh connector/client does not recover it;
- restarting the ESPHome proxy does not recover it;
- only a WC power cycle restored 0x59 in the reproduced stuck state;
- WRITE_0 and WRITE_1 both use ATT Write Without Response;
- adding up to 30 ms between FIRST and CONS did not fix the failure;
- CONTROL acknowledgements showed the WC acknowledging both transport frames;
- the GetSPL response parser's DTO field `a` is not a reliable record-count interpretation.

## Disproved or superseded explanations

The following earlier explanations should no longer be treated as the root cause:

- "Only an iPhone session can trigger the stuck state."
- "The bridge cannot trigger it."
- "Parameters 12 and 13 are themselves invalid."
- "A specific duplicate parameter 4 is the cause."
- "The continuation frame is simply lost."
- "The GATT write type is wrong."
- "A small FIRST→CONS delay is all that is missing."
- "Intermediate RPCs after GetSPL are required to trigger the failure."

The bridge reproduced the failure directly and the M-series test isolated the combined request
as the discriminator.

## Production validation

After deploying the split polling implementation, multiple normal on-demand polls completed
successfully on RS30.0 TS206.

A later explicit REST request to:

```text
GET /data/filter-status
```

also succeeded after those polls and returned valid filter data. This is the production
confirmation that the split polling workaround preserves `GetFilterStatus`.

## Recovery

If the device is already in the reproduced stuck state, reconnecting the bridge is not enough.
Power-cycle the WC once. After that, the split polling implementation avoids re-triggering the
known failure in normal operation.

## Implementation rule

For the tested RS30.0 TS206 bridge path:

> Keep each GetSystemParameterList request at **8 or fewer meaningful parameter IDs**.

The fixed 13-byte request payload may still contain zero padding in the continuation area; that
was safe in the tests. The dangerous condition observed here is meaningful/non-zero request
content extending into CONS.

This is an empirical interoperability rule for this firmware/transport combination, not a claim
about the abstract protocol specification.

## Separate semantic issue: SPL 12/13 labels

The filter-status bug and the semantic naming of SPL 12/13 are separate issues.

Current protocol documentation identifies:

- SPL 12 = `UnpostedShowerCycles`
- SPL 13 = `DaysUntilNextDescale`

The bridge historically exposes the returned values through fields named
`LidOffsetPosition` / `ShowerArmOffsetPosition`. Those external names are intentionally left
unchanged by the filter-status fix to avoid silently breaking existing MQTT/FHEM consumers.

The semantic cleanup remains tracked separately in `docs/roadmap.md`.
