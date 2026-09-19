# 2026-09-19 — pi5-beta: why PoE killed Wi-Fi (Volumio "Single Network Mode")

**Device:** `pi5-beta` · **Follows:** `2026-09-19-now-playing-1.1.1.md`,
`2026-09-19-forecast-days-patch.md` · **Status:** root cause found, FIX APPLIED,
PoE re-test pending

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
| PoE re-test | **pending** — see below |

## Re-test procedure

Keep `poe-diag.service` armed (it captures a report per boot), then: clean shutdown →
unplug USB-C → plug PoE → boot. Expected: `wlan0` gets an IP *and* eth0 has carrier.

**If Wi-Fi still does not come up on PoE:** the hotspot fallback will *not* rescue it,
because the daemon sees a wired carrier and assumes the LAN is fine. Recovery is to
power off and return to USB-C power, then read the new `/data/poe-diag-*.txt`.

## Open items

1. **Soak test.** The PoE reading of "0 undervoltage events" is from 60 s of uptime —
   encouraging, but a multi-hour PoE run is needed before calling the supply
   conclusively better. If it holds, PoE also removes the UV-driven instability
   suspected in the black-screen incidents.
2. **Does `/volumio/.env` survive a Volumio system update?** The file is dated with the
   current system build, so an update may replace it. Re-check and re-apply after any
   update.
3. **Upstream shape.** Two defensible upstream issues here: (a) carrier is a weak proxy
   for "the LAN works" (no DHCP lease / no route is the actionable signal), and (b) the
   hotspot fallback is skipped in exactly the situation where it is most needed.
   **Nothing has been filed** — a separate, explicit decision.
4. `hotspot_fallback` is currently **false**; consider enabling it as a general
   lock-out insurance for cases where there is no wired carrier either.
