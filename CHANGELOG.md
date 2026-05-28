DriveNets Fast Capture — CHANGELOG
==================================

v2026.05.28.2  (2026-05-28)
  * FIX: upload target is now LOCKED to dn@zkeiserman-dev:/home/dn/capture
    for every user. Previously each Mac cached its own dev VM (e.g.
    odahan02-dev, eduardhaimov-dev) as the target, which has no upload
    account and so the scp failed with "Permission denied". The team
    staging server is the only host with the right account + directory.
    Old cached host/user values in ~/Downloads/dn_devices.json are
    silently refreshed to the locked values on first run.
  * Only the password is still env-var-overridable (DN_SERVER_PASSWORD)
    in case the team password rotates. Defaults to Drive1234! so no
    prompt is shown on a clean machine.

v2026.05.28.1  (2026-05-28)
  * FIX: download from dev VM was hanging on a TTY password prompt when SSH
    keys are not set up. The keyless rsync attempt now runs with
    BatchMode=yes (and StrictHostKeyChecking=no) so it fails fast and falls
    through to the expect-based password attempt instead of stealing the
    terminal. The scp fallback also gained StrictHostKeyChecking=no.
  * FIX: typing a password into the "Dev VM hostname" prompt by accident
    used to be cached as the server host forever. Hostnames are now
    validated (allowed chars: [A-Za-z0-9._-]); invalid input is rejected
    and prompted again, and any pre-existing invalid cached host is
    discarded as if unset.

v2026.05.24.1  (2026-05-24)
  * FIX: Dev VM upload now works for any user (not only zkeiserman-dev). On first
    run prompts for dev VM hostname + password and saves to ~/Downloads/dn_devices.json.
  * Reads ~/.dn_qa_env and DN_DEV_VM_* / DN_SERVER_* env vars automatically.
  * Creates upload directory on dev VM before capture; verifies file landed after scp.
  * Mac download tries SSH keys first, then password. Clear error when upload fails.
  * Routing-engine path uses same dev VM config (no longer hardcoded to 10.10.75.210).
  * No passwords hardcoded in the script.

v2026.05.20.6  (2026-05-20)
  * FIX: Routing-engine capture works when connecting by IP address, not only
    by serial. Container lookup falls back to any *_routing-engine.*.* name
    when the device_host prefix does not match (containers are named by serial).

v2026.05.20.5  (2026-05-20)
  * Reverted v2026.05.20.4: saved settings again reuse Target (routing/datapath)
    when you answer y to "Use these settings?".

v2026.05.20.4  (2026-05-20)
  * REVERTED - forced "Capture from" prompt on every run.

v2026.05.20.3  (2026-05-20)
  * Auto-update SSH now uses your local SSH keys (Mac agent / ~/.ssh) so
    silent install actually succeeds without DN_SERVER_PASSWORD.

v2026.05.20.2  (2026-05-20)
  * Auto-update now installs silently when a newer version is available
    (no "Install this update now?" prompt). Set DN_SKIP_UPDATE_CHECK=1 to
    disable the update check entirely.

v2026.05.20.1  (2026-05-20)
  * FIX: "Capture from" (Datapath vs Routing Engine) is now asked before NCP
    auto-detection. NCP detection only runs for datapath captures on cluster
    devices; routing engine captures no longer trigger port-mirroring queries.

v2026.04.25.5  (2026-04-25)
  * FIX: live "Size: x.x/10240MB" was always 0 because wbox-cli buffers and
    only flushes the pcap on close. Now uses df-delta on /tmp (snapshot
    used-bytes before opening the pcap, poll every 5s, report current_used
    minus baseline). Falls back to the direct file stat if it ever becomes
    non-zero. The 10 GB cap now actually enforces during capture.

v2026.04.25.4  (2026-04-25)
  * Ctrl+C during a capture now removes the in-progress /tmp/<file>.pcap
    on the device automatically (and sweeps /tmp/*.pcap as a safety net),
    so an interrupted run no longer leaves leftovers behind.
  * Same protection added to the routing-engine path (deletes the file
    from inside the container).
  * Both interrupt handlers now safely no-op if Ctrl+C is hit before the
    pcap or shell variables are even created.

v2026.04.25.3  (2026-04-25)
  * Live MB counter is now shown on the progress line in BOTH timed and
    infinite capture modes (it was only shown in infinite mode before).
    Example:  Progress: 35% Duration: 99999999s (34/99999999)  Size: 12.4/10240MB

v2026.04.25.2  (2026-04-25)
  * Smart single-arg parsing: `dn_capture.py 10` now means "capture for 10
    seconds" (auto prefix "capture") instead of "infinite capture with prefix
    '10'". Previous two-arg usage is unchanged.
  * Self-update mechanism: every run checks dev VM for a newer master copy,
    shows this changelog, and offers to install. Disable with
    DN_SKIP_UPDATE_CHECK=1.

v2026.04.25.1  (2026-04-25)
  * All capture paths moved from /var/tmp -> /tmp on the device.
  * Pre-flight: refuses to start a capture unless >= 15 GB free on /tmp,
    with cleanup hint in the error.  Override via DN_MIN_FREE_GB.
  * Per-capture size cap of 10 GB; capture stops cleanly when reached and
    the partial pcap is still uploaded. Override via DN_MAX_PCAP_MB.
  * Live progress now shows MB consumed (e.g. "Capturing... 12s, 47.3/10240MB").
  * Atomic device->server upload: scp && rm so the device-side file is
    deleted only when the upload succeeded.
  * Atomic server->Mac download: rsync --remove-source-files (with scp
    fallback) so the server-side file is deleted only when the download
    succeeded. fast_cleanup_server is now a harmless fallback.
  * On entry to the datapath shell, leftover /tmp/*.pcap from previous
    interrupted runs are removed before the new capture starts.

v2026.04.23.1  (2026-04-23)
  * Pre-existing baseline before today's hardening work.
