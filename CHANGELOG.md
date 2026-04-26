DriveNets Fast Capture — CHANGELOG
==================================

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
