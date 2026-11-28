# User data

A cloud hands a new instance its configuration as *user data*, served by a metadata
service. kaetzli fetches and runs it with `kaetzli-user-data.service`, a bash script and
curl (no cloud-init, which is Python).

## Turning it on

The unit runs only when the kernel command line names a source:

| Argument | Fetched from |
| --- | --- |
| `kaetzli.user-data=openstack` | `http://169.254.169.254/openstack/latest/user_data` |

A VM booted from `rootfs.erofs` has no such argument, so the unit is skipped and nothing
is sent. A disk image built for a cloud adds it to the UKI's command line.

## What runs

- The user data must be a script: `#!` on its first line. It runs as root.
- Anything else, cloud-init's `#cloud-config` included, is logged as a warning and not
  run.
- It runs once per instance: the UUID from `meta_data.json` is recorded in
  `/var/lib/kaetzli-user-data/done` when the script succeeds. With `/var` on a disk a
  reboot does not run it again; with `/var` in RAM every boot does. A failing script
  records nothing and fails the unit, so the next boot retries.
- The unit is ordered after `network-online.target` and before `multi-user.target` is
  reached. A script that starts a unit ordered after `multi-user.target` must use
  `systemctl start --no-block`, or the boot waits for itself.

No config drive: the kernel has no ISO 9660 support, and the metadata service carries the
same files.

## Debugging

```sh
journalctl -b -u kaetzli-user-data
cat /run/kaetzli-user-data/user_data      # what was fetched (root only)
```

`./test/user-data.py` boots the image against a local stand-in for the metadata service.
