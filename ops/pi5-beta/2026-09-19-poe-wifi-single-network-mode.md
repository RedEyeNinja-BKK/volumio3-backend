# 2026-09-19 — pi5-beta: why PoE killed Wi-Fi (Volumio "Single Network Mode")

**Device:** `pi5-beta` · **Follows:** `2026-09-19-now-playing-1.1.1.md`,
`2026-09-19-forecast-days-patch.md` · **Status:** root cause found, FIX APPLIED and
**VERIFIED live on PoE** (PoE power + working Wi-Fi simultaneously)

## Symptom

Booting on PoE: the board comes up normally (the Now Playing idle screen renders —
later confirmed in the boot evidence), but there is **no Wi-Fi station and no
hotspot**, so the box is completely unreachable. The PoE router carries no data, so
there is no wired fallback either.

## Root cause

Volumio's wireless daemon (`/volumio/app/plugins/system_controller/network/wireless.js`,
"Volumio Wireless Daemon v4.0-rc5") runs in **Single Network Mode by default**, and
decides which network to use by reading **carrier only**:

```js
var carrier = fs.readFileSync('/sys/class/net/eth0/carrier', 'utf8').trim();
if (carrier === '1') { actualState = 'connected'; }
```

With a wired carrier present it does not bring Wi-Fi up as a link:

```js
} else if (singleNetworkMode && isWiredNetworkActive) {
    loggerInfo('Single Network Mode: Ethernet active, maintaining WiFi scan capability');
    keepWlanUpWithoutIP(...)          // wlan0 kept up WITHOUT an IP
```

and the hotspot fallback is skipped on the same assumption:

```js
} else {
    // Ethernet is UP - system is accessible via LAN
    loggerInfo('WiFi connection failed, but system accessible via ethernet');
```

**The design assumption is that link-up means the LAN is usable.** A PoE HAT
*requires* the Ethernet cable plugged in (power comes from the jacket pairs), so
eth0 always has carrier — and a dead-end PoE router with no DHCP and no uplink looks
exactly like a healthy LAN to this check. Wi-Fi is therefore suppressed by design.

## Measured evidence: PoE power is *better*, not worse

The boot-time capture (`/data/poe-diag-*.txt`, taken 60 s after each boot) settled
this, and it **corrects an earlier assessment on this device** that PoE would be a
downgrade versus a 27 W USB-C supply. It is the opposite:

| | PoE boot | USB-C baseline |
|---|---|---|
| `vcgencmd get_throttled` | **`0x0`** | `0x50000` |
| Undervoltage events since boot | **0** | **326** (in ~12 h) |
| `eth0` | UP, 1000 Mb/s link | DOWN |
| `wlan0` | DOWN (suppressed) | UP |
| `wpa_supplicant` / `dhcpcd` | active | active |
| `brcmfmac` | loaded (19 log lines) | loaded |

So the Wi-Fi failure was never a power problem: the driver loaded, the services ran,
and the power rails were *cleaner* on PoE. Only the interface was not brought up.

## Fix applied

`/volumio/.env`, line 26:

```
- SINGLE_NETWORK_MODE=true
+ SINGLE_NETWORK_MODE=false
```

The daemon documents this switch itself:

```
// Note: Single Network Mode is ON by default for production
// Set SINGLE_NETWORK_MODE=false in .env to allow multi-network mode
```

The match is a substring test (`envParameters.includes('SINGLE_NETWORK_MODE=false')`).

Backup: `np-backups/dotenv-backup-<timestamp>` on the device (841 bytes, 27 keys).

## Verification

| Check | Result |
|---|---|
| File change | one line, key count unchanged (27), diff reviewed |
| Daemon parses it | `/tmp/wireless.log`: `Multi-Network Mode enabled (development) - both ethernet and wireless can be active simultaneously` |
| Inert without a cable | after `systemctl restart wireless.service` on USB-C: `wlan0 UP`, default route intact, no loss of access |
| PoE re-test | **PASS** — see below |

### PoE re-test result (PASS)

Clean software power-off → USB-C removed → PoE cable only → boot. The board came up
with **both interfaces active at once**, which the original behaviour made impossible:

| Check | Result |
|---|---|
| `eth0` | UP, `carrier=1`, 1000 Mb/s — confirms PoE power path |
| `wlan0` | **UP with a DHCP address** |
| Default route | via `wlan0` (the dead-end eth0 took no address, as expected — no route theft) |
| `vcgencmd get_throttled` | **`0x0`** |
| Undervoltage events | **0** (USB-C boot the same day: `0x50005`, 1–326 events) |
| Internet over Wi-Fi while on PoE | yes — weather (Open-Meteo) and the Unsplash background both loaded on the idle screen |
| `wireless.service` | active |

Boot log (`/tmp/wireless.log`) shows the decision explicitly:

```
Multi-Network Mode enabled (development) - both ethernet and wireless can be active simultaneously
Single Network Mode: disabled
refreshEthernetState: Corrected ethernet state: connected
Previous ethernet state: disconnected -> New ethernet state: connected
```

### Reading the log line that looks alarming

The transition still logs `Action: Switch to ethernet (WiFi scan mode)`. That message is
printed **unconditionally** when a wired carrier appears, but the *destructive* part is
gated:

```js
loggerInfo("Action: Switch to ethernet (WiFi scan mode)");   // always logged
if (!isFirstStart && singleNetworkMode) {                    // gate
    execSync(SUDO + ' ' + DHCPCD + ' -k ' + wlan, ...)       // lease release + teardown
}
```

With `singleNetworkMode` false the teardown is skipped, `wlan0` keeps its lease, and the
only trace is the misleading log line. Do not diagnose a Wi-Fi loss from that line alone —
check whether `wlan0` actually holds a DHCP address.

## Re-test procedure (executed, PASS)

Keep `poe-diag.service` armed (it captures a report per boot), then: clean software
power-off → unplug USB-C → plug PoE → boot. Result: `wlan0` got an IP *and* eth0 held
carrier, as recorded above.

**If Wi-Fi still does not come up on PoE:** the hotspot fallback will *not* rescue it,
because the daemon sees a wired carrier and assumes the LAN is fine. Recovery is to
power off and return to USB-C power, then read the new `/data/poe-diag-*.txt`.

## Open items

1. **Soak test.** Two clean PoE readings (`0x0` / 0 events, one at 60 s and one at boot)
   versus consistent undervoltage on the USB-C supply (`0x50005`, 1–326 events). Still
   worth a multi-hour PoE run before declaring the supply conclusively better — if it
   holds, PoE also removes the UV-driven instability suspected in the black-screen
   incidents.
2. **Software power-off is a stop, not a power cut** (operator-reported, code-confirmed):
   after a Volumio UI power-off the **display stays lit** and re-powering too soon can
   wedge the board. Volumio runs `systemctl power-off` with a `/sbin` fallback
   (`app/platformSpecific.js:17,24`), which brings the OS down but does **not** remove
   the 5 V input — so the panel and rails stay energised. Recovery rule: after a
   software power-off, physically break the supply and wait before restoring it.
   *Mitigated:* the panel is now blanked as part of the kiosk stop — see
   `2026-09-19-blank-display-on-power-off.md`.
2. **Does `/volumio/.env` survive a Volumio system update?** The file is dated with the
   current system build, so an update may replace it. Re-check and re-apply after any
   update.
3. **Upstream shape.** Two defensible upstream issues here: (a) carrier is a weak proxy
   for "the LAN works" (no DHCP lease / no route is the actionable signal), and (b) the
   hotspot fallback is skipped in exactly the situation where it is most needed.
   **Nothing has been filed** — a separate, explicit decision.
4. `hotspot_fallback` is currently **false**; consider enabling it as a general
   lock-out insurance for cases where there is no wired carrier either.
