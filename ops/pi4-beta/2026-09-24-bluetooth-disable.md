# 2026-09-24 — pi4-beta: Bluetooth disabled by configuration (no audio consumer)

**Device:** `pi4-beta` (Raspberry Pi 4, Volumio 4.204)
**Scope:** one configuration file (`/etc/bluetooth/main.conf`). No package, unit, plugin or
media change. No upstream source change.
**Status:** APPLIED + VERIFIED

## Why

The device ran a Bluetooth stack that would accept any paired device and stay discoverable
and pairable indefinitely, although **nothing on the device consumes a Bluetooth audio
stream**. Measured before the change:

| Observation | Result |
|---|---|
| `bluealsa-aplay.service` | masked |
| Bluetooth-related Volumio plugins | 0 |
| `bluealsa` backend references | 0 |
| vendor Bluetooth helper service | disabled / inactive |
| `bluetooth.service` | enabled / active |
| adapter state | `Powered: yes`, `Discoverable: yes`, `Pairable: yes` |
| advertising bounds | `DiscoverableTimeout 0`, `PairableTimeout 0`, `AlwaysPairable true` |
| auto-enable | `AutoEnable=true` |
| a paired handset | connected |

The configuration file on this device was byte-identical to the file on `pi5-beta` *before*
that device was fixed on 2026-09-23 (same single provenance), so the remedy here is applied
byte-identically rather than re-derived.

## Change

Four directives in `/etc/bluetooth/main.conf`:

| Directive | Before | After |
|---|---|---|
| `DiscoverableTimeout` | `0` | `180` |
| `PairableTimeout` | `0` | `180` |
| `AlwaysPairable` | `true` | `false` |
| `AutoEnable` | `true` | `false` |

`AutoEnable=false` is the load-bearing change: the adapter no longer comes up powered after a
Bluetooth service start. The timeouts bound advertising if the adapter is ever powered on
deliberately.

Applied by generating a candidate file unprivileged, reviewing it by byte-equality against
the file already accepted on `pi5-beta`, and placing it with a privileged copy. The original
file is retained on the device as `/etc/bluetooth/main.conf.bak-20260924`.

## Verified

| Observation | Result |
|---|---|
| resulting file hash | equals the value already in service on `pi5-beta` |
| lines changed | exactly the four above, nothing else |
| adapter after restart | `Powered: no`, `Discoverable: no` — same as `pi5-beta` |
| connected devices | 0 |
| Bluetooth service journal | no error lines since the restart |
| adapter powered off without manual action | yes (service-level fresh start) |
| `bluetooth.service` | left **enabled / active**, identical to `pi5-beta` |
| vendor Bluetooth helper service | unchanged (disabled / inactive) |
| ALSA chain | hash unchanged, full chain still defined |
| plugin ALSA contribution | hash unchanged |
| player / backend / network client | all active, network mounts unchanged |
| failed systemd units | 0 |

**Decision:** the service is deliberately left enabled on both devices so Bluetooth remains
available on request (`bluetoothctl power on`), while the adapter no longer advertises or
auto-enables by itself. No unit was masked or disabled.

## Hypothesis (not proven)

Persistence across a **cold boot** rests on the documented `AutoEnable` semantics. What is
proven here is a *service-level* fresh start (the adapter came up unpowered when the service
was restarted under the new file). `pi5-beta` carries the same file and has held across
restarts; the boot case has not been measured on either device.

## Attempted and reverted

Nothing.

## Open

None.

## Rollback

```sh
# on pi4-beta
cp /etc/bluetooth/main.conf.bak-20260924 /etc/bluetooth/main.conf
systemctl restart bluetooth.service
```

Restoring the backup returns the device to advertising a capability nothing consumes; that is
a deliberate regression to prior behaviour, not a repair.
