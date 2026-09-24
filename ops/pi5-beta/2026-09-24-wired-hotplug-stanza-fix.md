# 2026-09-24 — pi5-beta: failed hotplug unit each boot (wired interface stanza)

**Device:** `pi5-beta` (Raspberry Pi 5, Volumio 4.204)
**Scope:** one line of `/etc/network/interfaces`. No service, package or plugin change.
**Status:** APPLIED + VERIFIED

## Why

Every boot left one systemd unit in a failed state. The cause was a mismatch between the
stanza for the **wired** interface and the device's actual network role:

* the device's **uplink is the wireless** interface, which carries a static address;
* the wired interface has link but **no address and no DHCP server** behind it;
* because its stanza said `inet dhcp`, the hotplug handler on the wired interface started a
  DHCP client, which had nothing to talk to, spent ~45 seconds retrying and then left the unit
  failed.

The sibling device (`pi4-beta`) already used `inet manual` for the same interface and did not
exhibit the failure — that was the reference for the fix.

## Change

One line, in the wired interface stanza:

```diff
- iface <wired> inet dhcp
+ iface <wired> inet manual
```

The interface's `allow-hotplug` line was left as-is, matching the sibling device. The original
file is retained on the device as `/etc/network/interfaces.bak-20260924`.

Applied by generating the candidate unprivileged, asserting the result contained exactly one
`manual` stanza and zero `dhcp` stanzas for that interface, and placing it with a privileged
copy (the device service account has no interactive shell privilege).

## Verified

| Observation | Result |
|---|---|
| changed lines | exactly one (confirmed by diff against the retained backup) |
| hotplug unit started by hand | **success**, runtime 0.05 s (previously a ~45 s failure) |
| unit state after the test | not failed |
| address on the wired interface | none, as intended |
| wireless interface address | unchanged (static value intact) |
| default route | still via the wireless interface |
| gateway reachability | ping succeeds |
| stray DHCP client for the wired interface | none |

## Notes

* The remaining client process visible for the wired interface belongs to the system's
  central DHCP manager, which tracks every interface; it is not a second client spawned by the
  hotplug handler, and it holds no lease. Recorded so it is not mistaken for a leftover.
* The file's permissions were unusual (`777`) before and after the change; content and mode were
  both preserved (the copy targets an existing file, so the mode is unchanged).

## Hypothesis (not proven)

The failure would have recurred on every boot before this change (the unit is triggered by the
interface's hotplug rule). The fix is verified at the unit level; the next cold boot is the
first opportunity to confirm it in the boot path.

## Attempted and reverted

Nothing.

## Open

None.

## Rollback

```sh
# on pi5-beta
cp /etc/network/interfaces.bak-20260924 /etc/network/interfaces
systemctl reset-failed <hotplug-unit>
```

Restoring the prior file reinstates the boot-time DHCP attempt for an interface with no DHCP
server, which is the failure this record removes.
