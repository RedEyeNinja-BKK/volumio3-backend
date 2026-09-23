# pi5-beta — Bluetooth (volumiobt): investigation and disable

**Date:** 2026-09-23 (device local time, Asia/Bangkok)
**Device:** pi5-beta (Volumio, Pi 5)
**Authority:** operator request — "investigate bluetooth on the pi5-beta… it keeps asking to pair but it doesn't
really work, so you can disable it if it can't be fixed."
**Outcome:** Bluetooth is **disabled** (adapter not powered at boot, service disabled/inactive, config knobs
reverted to upstream defaults). The feature **cannot be made to work** in the current install: the pairing
agent exists only while the disabled service runs, and **nothing in the system consumes the A2DP stream**.
**Rollback:** see §Rollback (single-file restore, one config backup per step).

---

## 1. Verdict

| Question | Answer | Basis |
|---|---|---|
| Does a pairing agent exist? | Only while `volumiobt.service` runs | `PROVEN` — agent is `/local/a2dpagent`, registered by `/bin/bt/a2dp-agent` (D-Bus, dies with the service) |
| Can a paired phone play audio through this device? | **No** | `PROVEN` — no A2DP consumer exists (§3) |
| Why did it "keep asking to pair"? | Adapter was left **discoverable with no timeout** plus bluez's own default `Pairable: yes` | `PROVEN` (config semantics + control experiment) / `INFERRED` (phone-side sequence) |
| Fixable in place? | No, not without building an A2DP consumer into the DSP chain | `PROVEN` for the missing consumer; the consumer itself is a separate workstream |

## 2. Pre-change state

- `volumiobt.service` — **disabled, inactive** (the state it was found in; it is not enabled by default here)
- `bluetooth.service` — enabled, active
- `bluealsa.service` — active; `bluealsa-aplay.service` — **masked**, inactive (dead)
- No device has **ever** bonded: `/var/lib/bluetooth/<adapter>/` contains only `attributes/`, `cache/`,
  `settings`; `settings` holds `[General] Discoverable=false` and no device entries

`/etc/bluetooth/main.conf` (original, sha `4f85fe27…`) carried a deliberately permissive audio-sink posture:

```
Name = Volumio
Class = 0x20041C
DiscoverableTimeout = 0      # 0 = never time out, i.e. discoverable forever once enabled
PairableTimeout = 0
AlwaysPairable = true
JustWorksRepairing = always
ReverseServiceDiscovery = true
[Policy]
AutoEnable=true
```

`/bin/bt/btstart.sh` (called by `ExecStart`) does `discoverable on` + `pairable on`, registers a
NoInputNoOutput agent, then execs `/bin/bt/a2dp-agent`.

`/bin/bt/a2dp-agent` (234 lines) is a **BlueZ Agent1 only** — `Release`, `AuthorizeService` (accepts audio
UUIDs, rejects others), `RequestPinCode` ("0000"), `RequestPasskey` (0), `RequestConfirmation` (always accept),
`RequestAuthorization` (always accept), `Cancel`. It carries no media and no audio, and exits with the service.

## 3. Why audio cannot work (the decisive finding)

There is **no consumer for the A2DP stream** anywhere in the install:

- `bluealsa-aplay.service` is **masked** (`Loaded: masked … Active: inactive (dead)`); the binary
  `/usr/bin/bluealsa-aplay` exists but no enabled unit path reaches it.
- No Bluetooth plugin is installed. `/data/plugins` = `audio_interface/{audio_keepalive,fusiondsp}`,
  `music_service/{spop}`, `system_controller/{rpi_eeprom_config,rpi_eeprom_updater}`,
  `user_interface/{now_playing,peppymeter_screensaver,Systeminfo,touch_display}`.
- The Volumio backend has no bluetooth module and **zero** `bluealsa` references — only i18n strings match
  "bluetooth".
- `/etc/asound.conf` (sha `aa1f4ee7…`, unchanged throughout) contains no bluealsa reference; the device's
  ALSA default is the FusionDSP/camilladsp path, which an A2DP tap would have to be integrated with — not
  attempted here.

So: pairing may complete, but silence is guaranteed. That is the "doesn't really work" half.

## 4. Why it "keeps asking to pair" (mechanism)

1. `DiscoverableTimeout = 0` means *never time out*: once `btstart.sh` ran `discoverable on`, the adapter
   advertised indefinitely — including after the service was later stopped/disabled, because the property
   lives in the running `bluetoothd`, not in the service.
2. With the service inactive there is **no registered agent**, so incoming pairing attempts cannot complete.
3. `Pairable: yes` at boot is **bluez 5.83's own default**, not this device's config. Control: with
   `/etc/bluetooth/main.conf` moved aside entirely and `bluetoothd` restarted, the adapter still reported
   `Powered: yes, Pairable: yes, Discoverable: no`.
4. Net effect (`INFERRED`, no phone-side evidence and only one retained boot of journal): the phone sees a
   permanently discoverable, audio-class device named "Volumio", offers to pair, and every attempt fails or
   produces no audio — so it keeps offering.

## 5. Changes applied

| Item | Before | After |
|---|---|---|
| `volumiobt.service` | disabled, inactive | **disabled, inactive** (baseline restored; a temporary `enable --now` was used only to observe the start path, then disabled again) |
| `DiscoverableTimeout` | `0` (forever) | `180` (upstream default) |
| `PairableTimeout` | `0` | `180` |
| `AlwaysPairable` | `true` | `false` |
| `AutoEnable` | `true` | `false` |
| Adapter posture | powered / discoverable / pairable | **not powered / not discoverable** |

`/etc/bluetooth/main.conf` final sha256 `a0b796177afe2f2d7cbf054c19429e9f0f4f5a73bfe78a69a80a9802b5ab1f5a`
(644 root:root). Edits were made by asserted-anchor generation in `/tmp` + `sudo /bin/cp`, never by in-place
regex, and each generation asserted the precondition hash and a single anchor match.

Backups on the device (every one byte-verified against the value it replaced):

| File | sha256 | Content |
|---|---|---|
| `/etc/bluetooth/main.conf.bak-20260923` | `4f85fe27…` | original as found |
| `/etc/bluetooth/main.conf.bak-20260923-r2` | `46a1e3c4…` | after DiscoverableTimeout/AlwaysPairable revert |
| `/etc/bluetooth/main.conf.bak-20260923-r3` | `04fda73b…` | after PairableTimeout revert |

## 6. Verification

- Generated candidate hash == installed hash (`a0b79617…`); `grep` confirms
  `DiscoverableTimeout = 180`, `PairableTimeout = 180`, `AlwaysPairable = false`, `AutoEnable=false`.
- After `systemctl restart bluetooth.service`: `Powered: no`, `Discoverable: no`; `volumiobt` = disabled/inactive.
- Control arm for §4.3: with no `main.conf` at all → `Powered: yes, Pairable: yes, Discoverable: no`
  (so `Pairable: yes` is not a config regression introduced here; it is the vendor default).
- **Nothing else moved**: `/etc/asound.conf` still `aa1f4ee7…`; audio path healthy
  (`healthy=true`, `degraded_probes=[]`, camilladsp/mpd/go-librespot present, DAC PCM `RUNNING`,
  57.9 °C, `throttled=0x0`, boot 22:33:48, uptime 27 min at check time).
- `bluealsa.service` and `bluetooth.service` remain active — untouched by this change.

## 7. Rollback

```
# restore any step (or the original), then re-enable the service if BT is wanted again
sudo /bin/cp /etc/bluetooth/main.conf.bak-20260923 /etc/bluetooth/main.conf   # original
sudo /bin/systemctl restart bluetooth.service
sudo /bin/systemctl enable --now volumiobt.service
```

Caveat: with `DiscoverableTimeout = 180` a re-enabled service advertises for 3 minutes per start rather than
indefinitely. Restore the original file to get the previous (forever-discoverable) behaviour back.

## 8. Residuals / open items

1. `bluetooth.service` + `bluealsa.service` stay enabled/active while the adapter is unpowered; nothing
   consumes bluealsa. Left as-is deliberately (smallest change; they are upstream defaults).
2. bluez 5.83 reports `Pairable: yes` whenever the adapter is powered. Not config-controlled, so it cannot be
   turned off durably from `main.conf` — it is only inert while the adapter stays unpowered.
3. Real Bluetooth audio on this device would require an A2DP consumer wired into the camilladsp/FusionDSP
   chain (the known two-writer FIFO hazard applies). That is a separate workstream with its own review; not
   attempted or authorised here.
4. Only one boot of journal was retained, so the phone-side sequence in §4.4 stays inferred, not observed.

## 9. Artifacts

`device-artifacts/` — byte-exact copies fetched from the device, with `LIVE-SHA256SUMS` and
`final-posture.txt` / `final-live-sha.txt` as the post-change read-back. Manifest: `MANIFEST-SHA256SUMS`.
