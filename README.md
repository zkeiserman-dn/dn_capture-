# dn_capture

Fast packet capture helper for DriveNets DNOS lab devices.

Connects to a DNOS device over SSH, drives `wbox-cli debug open pcap` (datapath)
or `tcpdump` inside the routing-engine container, transfers the resulting
`.pcap` to a staging server and then to the operator's local `~/Downloads/`,
and opens it in Wireshark.

Designed for fast turn-around debugging: typical run is `python3 dn_capture.py 30`.

## Requirements

- Python 3 with `paramiko` (`pip3 install paramiko`)
- `expect` (used by the SCP/rsync wrappers)
- Wireshark installed locally (only required for the auto-open step)

## Quick start

```bash
# 30-second capture, auto-named, default device from saved settings
python3 dn_capture.py 30

# named capture
python3 dn_capture.py mybug 30

# infinite capture (Ctrl+C to stop)
python3 dn_capture.py mybug inf
```

On first run the script will prompt for device hostname/serial, username, and
password, and remember them in `~/Downloads/dn_devices.json`.

## Modes

- **Datapath capture** — runs `wbox-cli debug open pcap` from the NCP datapath
  shell. Used for forwarding-plane troubleshooting on standalone or cluster
  systems (auto-detects, picks the NCP from port-mirroring config when on a
  cluster).
- **Routing-engine capture** — runs `tcpdump` inside the `routing-engine`
  container's `inband_ns` namespace. Used for control-plane / management-plane
  troubleshooting.

The script asks which mode you want on first run and remembers the choice.

## Safety nets

The script enforces several protections to prevent the disk-full → CLI-crash
failure mode that previously plagued the lab:

| Layer | What it does |
|---|---|
| **Pre-flight free-space check** | Refuses to start if `/tmp` has less than `MIN_FREE_GB` (default 15 GB). |
| **Hard size cap** | Capture is closed cleanly the moment the file crosses `MAX_PCAP_MB` (default 10 GB). |
| **Startup sweep** | Removes any leftover `/tmp/*.pcap` before opening the new pcap. |
| **Interrupt cleanup** | Ctrl+C deletes the in-progress pcap (and re-sweeps `/tmp/*.pcap`). |
| **Atomic transfers** | `scp && rm` (device→server) and `rsync --remove-source-files` (server→Mac) so source files are deleted only on a successful transfer. |

## Tunables (env vars)

| Var | Default | Effect |
|---|---|---|
| `DN_MAX_PCAP_MB` | `10240` | Max pcap size in MB before the capture stops itself. |
| `DN_MIN_FREE_GB` | `15` | Minimum free space required on `/tmp` before a capture is allowed to start. |
| `DN_SKIP_UPDATE_CHECK` | _unset_ | Set to `1` to disable the startup self-update check. |

Example:

```bash
DN_MAX_PCAP_MB=2048 python3 dn_capture.py mybug 600
```

## Self-update

Each run, the script briefly contacts the master copy on the dev VM
(`dn@zkeiserman-dev:/home/dn/dn_capture.py`), compares `__version__`, and if a
newer version exists it shows the changelog and offers to install in-place.
The previous version is saved as `dn_capture.py.bak`.

To roll back a bad update:

```bash
mv dn_capture.py.bak dn_capture.py
```

## File layout

```
dn_capture/
├── dn_capture.py         # the script
├── CHANGELOG.txt         # human-readable changelog (shown by the updater)
├── README.md             # this file
└── .gitignore
```

## Status / known limitations

- Hardcoded credentials for the staging server (`zkeiserman-dev`) are baked
  into the script. Replace with `DN_SERVER_PASSWORD` env var before making the
  repo public.
- Routing-engine capture path does not yet enforce the size cap (the tcpdump
  child blocks the only SSH session, so polling needs a second connection —
  to be added).
- Self-update currently fetches via SSH+SFTP. Could be migrated to a public
  GitHub raw URL once the repo is published.

## License

Internal tool — not yet licensed for external use.
