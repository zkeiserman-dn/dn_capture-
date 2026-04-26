#!/usr/bin/env python3

import paramiko
import time
import os
import sys
import subprocess
import getpass
import json
from datetime import datetime
import re

# Credentials file path
CREDS_FILE = os.path.expanduser("~/Downloads/dn_devices.json")

# Maximum pcap size (MB). Capture is stopped early when the file on the device
# crosses this threshold so a runaway capture cannot fill /tmp and break the
# device's CLI. Override at runtime: DN_MAX_PCAP_MB=20480 python3 dn_capture.py ...
MAX_PCAP_MB = int(os.environ.get("DN_MAX_PCAP_MB", "10240"))  # 10 GB

# Minimum free space (GB) required on /tmp before a capture is allowed to start.
# Must comfortably exceed MAX_PCAP_MB so the device never runs the partition
# dry mid-capture (10 GB cap + 5 GB safety margin = 15 GB minimum free).
MIN_FREE_GB = int(os.environ.get("DN_MIN_FREE_GB", "15"))

# ---------------------------------------------------------------------------
# Auto-update: every run, the script briefly contacts the master copy on the
# dev VM, compares versions, and if a newer one exists shows the changelog and
# offers to install it. The master copy lives at:
#   dn@zkeiserman-dev:/home/dn/dn_capture.py
#   dn@zkeiserman-dev:/home/dn/dn_capture_CHANGELOG.txt
# Disable the check entirely by exporting DN_SKIP_UPDATE_CHECK=1.
# ---------------------------------------------------------------------------
__version__ = "2026.04.25.5"
UPDATE_SERVER = "zkeiserman-dev"
UPDATE_USER = "dn"
UPDATE_PASS = "Drive1234!"
UPDATE_REMOTE_SCRIPT = "/home/dn/dn_capture.py"
UPDATE_REMOTE_CHANGELOG = "/home/dn/dn_capture_CHANGELOG.txt"


def _is_master_host():
    """Skip update check when running on the dev VM that hosts the master copy."""
    try:
        return os.uname().nodename == UPDATE_SERVER
    except Exception:
        return False


def _fetch_remote_version_and_changelog():
    """Return (remote_version, changelog_text) or (None, None) on any failure."""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(UPDATE_SERVER, username=UPDATE_USER, password=UPDATE_PASS,
                       look_for_keys=False, allow_agent=False, timeout=5,
                       banner_timeout=5, auth_timeout=5)
        _, stdout, _ = client.exec_command(
            f"grep -m1 '^__version__' {UPDATE_REMOTE_SCRIPT}")
        ver_line = stdout.read().decode(errors='ignore').strip()
        m = re.search(r'"([^"]+)"', ver_line)
        remote_version = m.group(1) if m else None
        _, stdout, _ = client.exec_command(f"cat {UPDATE_REMOTE_CHANGELOG} 2>/dev/null")
        changelog = stdout.read().decode(errors='ignore')
        client.close()
        return remote_version, changelog
    except Exception:
        return None, None


def _install_update():
    """Download new dn_capture.py, back up the current one, atomically replace it."""
    try:
        local = os.path.realpath(sys.argv[0] if sys.argv[0].endswith('.py') else __file__)
        backup = local + ".bak"
        new = local + ".new"

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(UPDATE_SERVER, username=UPDATE_USER, password=UPDATE_PASS,
                       look_for_keys=False, allow_agent=False, timeout=15)
        sftp = client.open_sftp()
        sftp.get(UPDATE_REMOTE_SCRIPT, new)
        sftp.close()
        client.close()

        import shutil, stat
        shutil.copy2(local, backup)
        os.replace(new, local)
        try:
            os.chmod(local, os.stat(local).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass
        print(f"\n  Updated to latest version. Backup saved at: {backup}")
        return True
    except Exception as e:
        print(f"\n  Update failed: {e}")
        return False


def _check_for_update():
    if os.environ.get("DN_SKIP_UPDATE_CHECK") == "1":
        return
    if _is_master_host():
        return  # we ARE the master copy
    print(f"Checking for updates (current v{__version__})...", end='', flush=True)
    remote_version, changelog = _fetch_remote_version_and_changelog()
    if not remote_version:
        print(" skipped (could not reach server)")
        return
    if remote_version == __version__:
        print(f" up to date")
        return
    print(f"\n\n  >>> NEW VERSION AVAILABLE <<<")
    print(f"     Your version  : {__version__}")
    print(f"     Latest version: {remote_version}\n")
    if changelog:
        print("  --- Changelog ---")
        for line in changelog.splitlines()[:30]:
            print(f"  {line}")
        print("  --- end of changelog ---\n")
    try:
        choice = input("Install this update now? (y/n) [y]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipping update.")
        return
    if choice in ('', 'y', 'yes'):
        if _install_update():
            print("  Re-launching with the new version...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        print("  Skipping update. (Set DN_SKIP_UPDATE_CHECK=1 to silence this prompt.)")

def load_saved_settings():
    """Load last used settings"""
    try:
        if os.path.exists(CREDS_FILE):
            with open(CREDS_FILE, 'r') as f:
                data = json.load(f)
                if 'last_settings' in data:
                    return data['last_settings']
    except:
        pass
    return None

def save_settings(mode, deployment, ncp, device_host, device_user, device_pass):
    """Save settings for next time"""
    try:
        data = {}
        if os.path.exists(CREDS_FILE):
            with open(CREDS_FILE, 'r') as f:
                data = json.load(f)
        
        data['last_settings'] = {
            'mode': mode,
            'deployment': deployment,
            'ncp': ncp,
            'device_host': device_host,
            'device_user': device_user,
            'device_pass': device_pass,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(CREDS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def detect_deployment_type(ssh_client):
    """Auto-detect if system is Standalone or Cluster"""
    try:
        print("Detecting system type...")
        
        # Use interactive shell for DriveNets CLI
        shell = ssh_client.invoke_shell()
        time.sleep(1)
        
        # Clear initial output
        if shell.recv_ready():
            shell.recv(65535)
        
        # Send show command
        shell.send('show system | include "System Type:"\n')
        time.sleep(1.5)
        
        # Collect output
        output = ""
        for _ in range(10):
            if shell.recv_ready():
                output += shell.recv(65535).decode('utf-8', errors='ignore')
                time.sleep(0.2)
            else:
                break
        
        shell.close()
        
        # Check if Cluster (CL-) or Standalone (SA-)
        if "CL-" in output:
            print(f"✓ Detected: CLUSTER")
            return 'cluster'
        elif "SA-" in output:
            print(f"✓ Detected: STANDALONE")
            return 'standalone'
        else:
            print("⚠ Could not detect system type from output")
            return None
            
    except Exception as e:
        print(f"⚠ Detection error: {e}")
        return None

def detect_ncp_from_port_mirroring(ssh_client):
    """Auto-detect NCP number from port-mirroring configuration"""
    try:
        print("Detecting NCP from port-mirroring config...")
        
        # Use interactive shell for DriveNets CLI
        shell = ssh_client.invoke_shell()
        time.sleep(1)
        
        # Clear initial output
        if shell.recv_ready():
            shell.recv(65535)
        
        # Send show command
        shell.send('show config services | flatten | include "services port-mirroring session"\n')
        time.sleep(3)  # Wait longer for command to complete
        
        # Collect output - wait for prompt to appear
        output = ""
        end_time = time.time() + 8
        while time.time() < end_time:
            if shell.recv_ready():
                chunk = shell.recv(65535).decode('utf-8', errors='ignore')
                output += chunk
                # Check if we got a prompt (indicates command finished)
                if '#' in chunk[-50:] or '>' in chunk[-50:]:
                    time.sleep(0.5)  # Get any remaining output
                    if shell.recv_ready():
                        output += shell.recv(65535).decode('utf-8', errors='ignore')
                    break
            else:
                time.sleep(0.3)
        
        shell.close()
        
        # Debug: Print raw output for troubleshooting (first 500 chars)
        if not output.strip():
            print("⚠ No output received from command")
            return None
        
        # Try multiple patterns to find NCP number
        # Pattern 1: destination-interface geXXX-X/0/Y (most specific, variable prefix)
        pattern1 = r'destination-interface\s+ge\d+-(\d+)/\d+/\d+'
        match = re.search(pattern1, output, re.IGNORECASE | re.MULTILINE)
        if match:
            ncp_number = match.group(1)
            print(f"✓ Auto-detected NCP: {ncp_number} (from destination-interface)")
            return ncp_number
        
        # Pattern 2: source-interface geXXX-X/0/Y (alternative)
        pattern2 = r'source-interface\s+ge\d+-(\d+)/\d+/\d+'
        match = re.search(pattern2, output, re.IGNORECASE | re.MULTILINE)
        if match:
            ncp_number = match.group(1)
            print(f"✓ Auto-detected NCP: {ncp_number} (from source-interface)")
            return ncp_number
        
        # Pattern 3: Any geXXX-X/0/Y pattern (more flexible, multiline)
        pattern3 = r'ge\d+-(\d+)/\d+/\d+'
        matches = re.findall(pattern3, output, re.IGNORECASE | re.MULTILINE)
        if matches:
            # Use the first match (usually destination-interface comes first)
            ncp_number = matches[0]
            print(f"✓ Auto-detected NCP: {ncp_number} (from interface pattern)")
            return ncp_number
        
        # Pattern 4: Very simple - any geXXX-X/ pattern
        pattern4 = r'ge\d+-(\d+)/'
        matches = re.findall(pattern4, output, re.IGNORECASE | re.MULTILINE)
        if matches:
            # Use the first match
            ncp_number = matches[0]
            print(f"✓ Auto-detected NCP: {ncp_number} (from interface name)")
            return ncp_number
        
        # Pattern 5: Look for any interface with format X/0/Y where X could be NCP
        # This catches cases where the interface might be formatted differently
        pattern5 = r'(\d+)/0/\d+'
        matches = re.findall(pattern5, output)
        if matches:
            # Filter out obviously wrong numbers (too large to be NCP, usually < 100)
            valid_ncps = [m for m in matches if int(m) < 100]
            if valid_ncps:
                ncp_number = valid_ncps[0]
                print(f"✓ Auto-detected NCP: {ncp_number} (from port pattern)")
                return ncp_number
        
        # Debug output - show what we found
        print("⚠ Could not auto-detect NCP")
        print(f"   Output length: {len(output)} chars")
        
        # Show relevant lines from output
        output_lines = output.split('\n')
        relevant_lines = []
        for line in output_lines:
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in ['port-mirroring', 'interface', 'ge', 'destination', 'source']):
                relevant_lines.append(line.strip())
        
        if relevant_lines:
            print("   Relevant output lines:")
            for line in relevant_lines[:5]:  # Show first 5 relevant lines
                print(f"   {line[:100]}")
        else:
            # Show first few lines of output
            print("   First lines of output:")
            for line in output_lines[:5]:
                if line.strip():
                    print(f"   {line[:100]}")
        
        return None
            
    except Exception as e:
        print(f"⚠ Detection error: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_connection_details(mode='datapath', deployment='standalone', ncp='0'):
    """Get device connection details (mode already selected in main)"""
    # Load last settings to retrieve stored credentials
    last_settings = load_saved_settings()
    
    if last_settings:
        device_host = last_settings.get('device_host') or last_settings.get('hostname')
        device_user = last_settings.get('device_user') or last_settings.get('username')
        device_pass = last_settings.get('device_pass') or last_settings.get('password')
        
        return {
            'mode': mode,
            'deployment': deployment,
            'ncp': ncp,
            'device_host': device_host,
            'device_user': device_user,
            'device_pass': device_pass,
            'server_host': "zkeiserman-dev",
            'server_user': "dn",
            'server_pass': "Drive1234!",
            'server_path': "/home/dn/capture"
        }
    
    # Fallback (shouldn't happen since main handles prompts)
    return {
        'mode': mode,
        'deployment': deployment,
        'ncp': ncp,
        'device_host': 'YC71F5VJ00008P2',
        'device_user': 'dnroot',
        'device_pass': 'dnroot',
        'server_host': "zkeiserman-dev",
        'server_user': "dn",
        'server_pass': "Drive1234!",
        'server_path': "/home/dn/capture"
    }

def show_progress(percentage, elapsed_seconds=None, duration=None,
                  current_second=None, size_mb=None, max_mb=None):
    """Show percentage, duration, current second, and (optionally) live pcap size."""
    size_part = ""
    if size_mb is not None and max_mb is not None:
        size_part = f"  Size: {size_mb:6.1f}/{max_mb}MB"
    if duration is not None and current_second is not None:
        print(f"\rProgress: {percentage}% Duration: {duration}s "
              f"({current_second}/{duration}){size_part}     ",
              end='', flush=True)
    elif duration is not None:
        print(f"\rProgress: {percentage}% Duration: {duration}s{size_part}     ",
              end='', flush=True)
    else:
        print(f"\rProgress: {percentage}%{size_part}     ", end='', flush=True)

def get_downloads_folder():
    """Get Downloads folder path"""
    import platform
    
    if platform.system() == "Darwin":
        return os.path.expanduser("~/Downloads")
    elif platform.system() == "Windows":
        return os.path.join(os.path.expanduser("~"), "Downloads")
    else:
        return os.path.expanduser("~/Downloads")

def fast_download(filename, config):
    """Download pcap from server to local Downloads, removing it from the
    server only if the transfer succeeded. Tries rsync --remove-source-files
    first (atomic move semantics); falls back to scp + ssh rm if rsync is not
    available on the local machine."""
    try:
        downloads_path = get_downloads_folder()
        local_file = f"{downloads_path}/{filename}"
        download_timeout = 60

        rsync_available = subprocess.run(
            "command -v rsync", shell=True, capture_output=True
        ).returncode == 0

        if rsync_available:
            rsync_cmd = (
                f'rsync -a --remove-source-files '
                f'{config["server_user"]}@{config["server_host"]}:'
                f'{config["server_path"]}/{filename} "{downloads_path}/"'
            )
            expect_script = f'''
expect -c "
set timeout {download_timeout}
spawn {rsync_cmd}
expect {{
    \\"password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    \\"Password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    eof
}}
"
'''
        else:
            scp_cmd = (
                f'scp {config["server_user"]}@{config["server_host"]}:'
                f'{config["server_path"]}/{filename} "{downloads_path}/"'
            )
            expect_script = f'''
expect -c "
set timeout {download_timeout}
spawn {scp_cmd}
expect {{
    \\"password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    \\"Password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    \\"100%\\" {{ exp_continue }}
    eof
}}
"
'''

        subprocess.run(expect_script, shell=True, capture_output=True,
                       text=True, timeout=download_timeout + 10)

        if os.path.exists(local_file) and os.path.getsize(local_file) > 24:
            mode = "rsync (auto-removed from server)" if rsync_available else "scp"
            print(f"Downloaded to: {local_file}  [via {mode}]")
            return True
        return False

    except Exception:
        return False

def fast_cleanup_server(filename, config):
    """Ultra fast server cleanup"""
    try:
        cleanup_script = f'''
expect -c "
set timeout 60
spawn ssh {config['server_user']}@{config['server_host']} rm -f {config['server_path']}/{filename}
expect {{
    \\"password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    \\"Password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    eof
}}
"
'''
        subprocess.run(cleanup_script, shell=True, capture_output=True, text=True, timeout=90)
    except:
        pass

def datapath_capture(device_host, device_user, device_pass, deployment, ncp):
    """Datapath capture (original standalone + cluster support)"""
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python3 dn_capture.py [filename_prefix] [duration_seconds]")
        print("Examples:")
        print("  python3 dn_capture.py 10           -> 10s capture, prefix='capture'")
        print("  python3 dn_capture.py zohar        -> infinite capture, prefix='zohar'")
        print("  python3 dn_capture.py zohar 30     -> 30s capture, prefix='zohar'")
        print("  python3 dn_capture.py zohar inf    -> infinite capture, prefix='zohar'")
        sys.exit(1)

    INF_TOKENS = ('inf', 'infinite', 'forever', '0')

    if len(sys.argv) == 2:
        a = sys.argv[1].lower()
        # Single arg: if it looks like a duration (number or inf-token), treat
        # it as duration with an auto-generated prefix so `dn_capture.py 10`
        # actually means "capture for 10 seconds".
        if a in INF_TOKENS:
            filename_prefix = "capture"
            duration = None
            print(f"\nInfinite capture mode - press Ctrl+C to stop")
        elif a.isdigit():
            filename_prefix = "capture"
            duration = int(a)
            print(f"\nDuration: {duration} seconds (auto prefix='capture')")
        else:
            filename_prefix = sys.argv[1]
            duration = None
            print(f"\nInfinite capture mode - press Ctrl+C to stop")
    else:
        filename_prefix = sys.argv[1]
        duration_str = sys.argv[2].lower()
        if duration_str in INF_TOKENS:
            duration = None
            print(f"\nInfinite capture mode - press Ctrl+C to stop")
        else:
            duration = int(duration_str)
    
    config = {
        'mode': 'datapath',
        'deployment': deployment,
        'ncp': ncp,
        'device_host': device_host,
        'device_user': device_user,
        'device_pass': device_pass,
        'server_host': "zkeiserman-dev",
        'server_user': "dn",
        'server_pass': "Drive1234!",
        'server_path': "/home/dn/capture"
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pcap_filename = f"{filename_prefix}_{timestamp}.pcap"
    
    print(f"\nTarget: {pcap_filename}")
    if duration:
        print(f"Duration: {duration} seconds")
    else:
        print(f"Duration: Infinite (Ctrl+C to stop)")
    
    try:
        # Ultra fast connection (5%)
        show_progress(5)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['device_host'], username=config['device_user'], password=config['device_pass'], timeout=10)
        
        # Lightning setup (10%)
        show_progress(10)
        shell = ssh.invoke_shell()
        time.sleep(0.3)
        if shell.recv_ready():
            shell.recv(4096)
        
        # helper
        def wait_for(prompts, timeout=6):
            buf = ""
            end_time = time.time() + timeout
            while time.time() < end_time:
                if shell.recv_ready():
                    buf_chunk = shell.recv(2048).decode(errors="ignore")
                    buf += buf_chunk
                    if any(p in buf for p in prompts):
                        return buf
                time.sleep(0.1)
            return buf

        # Fast configure (15%)
        show_progress(15)
        shell.send("configure\n")
        config_output = wait_for(["NCP", "(cfg)"], 3)
        
        # Exit configure if needed
        if "(cfg)" in config_output:
            shell.send("exit\n")
            wait_for(["NCP"], 2)
        
        # Quick shell access (20%) - Use configured NCP
        show_progress(20)
        ncp_number = config['ncp']
        access_cmds = [f"run start shell ncp {ncp_number}", f"start shell ncp {ncp_number}", 
                      f"run start shell host {ncp_number}", f"start shell host {ncp_number}"]
        in_datapath = False
        for cmd in access_cmds:
            shell.send(cmd + "\n")
            out = wait_for(["Password:", "root@datapath", "datapath"], 5)
            if "Password:" in out:
                shell.send(f"{config['device_pass']}\n")
                out = wait_for(["root@datapath", "datapath"], 5)
            if "datapath" in out or "root@datapath" in out or ")root@" in out:
                in_datapath = True
                break
        if not in_datapath:
            print("Failed entering datapath shell after retries")
            ssh.close()
            return
        print("Entered datapath shell successfully")

        # Remove leftover pcap files from previous runs that may have been
        # interrupted (script stuck / Ctrl+C) and never cleaned up.
        print("Removing leftover /tmp/*.pcap from previous runs...")
        shell.send("rm -f /tmp/*.pcap\n")
        time.sleep(0.3)
        if shell.recv_ready():
            shell.recv(4096)

        # Pre-flight: verify enough free space on /tmp before we open the pcap.
        # Bail out with a clean error message if not, so the user can clean it
        # up rather than discovering it mid-capture (or worse, after the CLI
        # crashes from a full partition).
        def get_free_gb(mount):
            try:
                shell.send(f"df -B1 {mount} | tail -1; echo __DF_END__\n")
                buf = ""
                end_t = time.time() + 4
                while time.time() < end_t:
                    if shell.recv_ready():
                        buf += shell.recv(8192).decode(errors="ignore")
                        if "__DF_END__" in buf:
                            break
                    else:
                        time.sleep(0.1)
                buf = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buf).replace('\r', '')
                for line in buf.splitlines():
                    parts = line.split()
                    if (len(parts) >= 4 and parts[1].isdigit()
                            and parts[2].isdigit() and parts[3].isdigit()):
                        return int(parts[3]) / (1024 ** 3)
            except Exception:
                return None
            return None

        free_gb = get_free_gb("/tmp")
        if free_gb is None:
            print(f"WARNING: could not read free space on /tmp; proceeding anyway")
        else:
            print(f"Free space on /tmp: {free_gb:.1f} GB (minimum required: {MIN_FREE_GB} GB)")
            if free_gb < MIN_FREE_GB:
                print()
                print("=" * 70)
                print(f"ERROR: insufficient free space on /tmp")
                print(f"  available : {free_gb:.1f} GB")
                print(f"  required  : {MIN_FREE_GB} GB")
                print(f"  capture cap: {MAX_PCAP_MB / 1024:.1f} GB")
                print()
                print("Please clean up /tmp on the device before running again:")
                print("  ssh dnroot@<device>          # bypass CLI if it's broken")
                print("  run start shell ncp 0")
                print("  ls -lhS /tmp/*.pcap | head   # see the offenders")
                print("  rm -f /tmp/*.pcap            # or be more selective")
                print("=" * 70)
                ssh.close()
                sys.exit(2)

        # CLEANUP (22%)
        show_progress(22)
        shell.send("wbox-cli close pcap\n")
        time.sleep(0.2)
        shell.recv(4096)
        shell.send("wbox-cli debug close pcap\n")
        time.sleep(0.2)
        shell.recv(4096)
        shell.send("wbox-cli debug dropped_packets disable\n")
        time.sleep(0.2)
        shell.recv(4096)
        
        # Fast enable (25%)
        show_progress(25)
        shell.send("wbox-cli debug dropped_packets enable\n")
        time.sleep(0.3)
        shell.recv(4096)
        
        # Quick capture start (30%)
        show_progress(30)
        capture_cmd = f"wbox-cli debug open pcap file /tmp/{pcap_filename}"
        
        capture_success = False
        for attempt in range(3):
            if attempt > 0:
                show_progress(30 + attempt)
                
            shell.send(capture_cmd + '\n')
            time.sleep(0.8)
            output = shell.recv(4096).decode()
            
            if "Trying to open pcap when it is open" in output:
                shell.send("wbox-cli close pcap\n")
                time.sleep(0.3)
                shell.recv(4096)
                shell.send("wbox-cli debug close pcap\n")
                time.sleep(0.3)
                shell.recv(4096)
                continue
            elif "Error" in output and "pcap" in output.lower():
                print(f"\nCapture failed! Output: {output}")
                ssh.close()
                return
            elif "pcap" in output.lower() or "file" in output.lower():
                capture_success = True
                break
            else:
                continue
        
        if not capture_success:
            print(f"\nCapture start failed after retries!")
            ssh.close()
            return

        # Live size monitor.
        #
        # `wbox-cli debug open pcap` does NOT flush incrementally to disk — the
        # file at /tmp/<file>.pcap stays at 0 bytes until close. So polling the
        # file directly via `stat` always shows 0. To get a live size we instead
        # poll *disk usage delta* on /tmp: snapshot used-bytes right before
        # opening the pcap, then `df` periodically and report the difference.
        # This catches whatever space the capture engine is consuming, regardless
        # of where it buffers it.

        def get_used_bytes_tmp():
            try:
                shell.send("df -B1 /tmp | tail -1; echo __DFU_END__\n")
                buf = wait_for(["__DFU_END__"], 3)
                buf = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buf).replace('\r', '')
                for line in buf.splitlines():
                    parts = line.split()
                    if (len(parts) >= 4 and parts[1].isdigit()
                            and parts[2].isdigit() and parts[3].isdigit()):
                        return int(parts[2])
            except Exception:
                return None
            return None

        def get_pcap_file_bytes():
            try:
                shell.send(f"stat -c %s /tmp/{pcap_filename} 2>/dev/null; echo __SZ_END__\n")
                buf = wait_for(["__SZ_END__"], 2)
                buf = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buf).replace('\r', '')
                for line in buf.splitlines():
                    s = line.strip()
                    if s.isdigit():
                        return int(s)
            except Exception:
                return None
            return None

        # Baseline of /tmp usage right BEFORE opening the pcap. The capture's
        # consumed bytes = current_used - initial_used.
        initial_used_bytes = get_used_bytes_tmp() or 0

        def get_pcap_size_bytes():
            # Prefer file stat (accurate per-file). Fall back to df-delta when
            # the engine hasn't flushed yet.
            file_b = get_pcap_file_bytes() or 0
            used_now = get_used_bytes_tmp()
            df_delta = 0 if used_now is None else max(0, used_now - initial_used_bytes)
            return max(file_b, df_delta)

        max_bytes = MAX_PCAP_MB * 1024 * 1024
        size_limit_hit = False
        poll_interval = 5  # seconds between size checks

        # Handle capture duration
        if duration is None:
            show_progress(35)
            print(f"\nCapture started! Max size: {MAX_PCAP_MB}MB. Press Ctrl+C to stop...")
            start_time = time.time()
            last_poll = 0
            current_mb = 0.0

            try:
                while True:
                    elapsed = int(time.time() - start_time)
                    if elapsed - last_poll >= poll_interval:
                        sz = get_pcap_size_bytes()
                        if sz is not None:
                            current_mb = sz / (1024 * 1024)
                            if sz >= max_bytes:
                                print(f"\nSize limit reached ({current_mb:.1f}MB >= {MAX_PCAP_MB}MB) — stopping capture")
                                size_limit_hit = True
                                break
                        last_poll = elapsed
                    print(f"\rCapturing... {elapsed}s elapsed, {current_mb:.1f}/{MAX_PCAP_MB}MB (Ctrl+C to stop)", end='', flush=True)
                    time.sleep(1)
            except KeyboardInterrupt:
                elapsed = int(time.time() - start_time)
                print(f"\nStopping capture after {elapsed} seconds...")
        else:
            show_progress(35, duration=duration)
            start_time = time.time()
            last_poll = 0
            current_mb = 0.0
            for sec in range(duration):
                progress = 35 + (sec * 20 // duration)
                current_sec = sec + 1
                elapsed = int(time.time() - start_time)
                if elapsed - last_poll >= poll_interval:
                    sz = get_pcap_size_bytes()
                    if sz is not None:
                        current_mb = sz / (1024 * 1024)
                        if sz >= max_bytes:
                            print(f"\nSize limit reached ({current_mb:.1f}MB >= {MAX_PCAP_MB}MB) — stopping capture early at {current_sec}/{duration}s")
                            size_limit_hit = True
                            break
                    last_poll = elapsed
                show_progress(progress, duration=duration, current_second=current_sec,
                              size_mb=current_mb, max_mb=MAX_PCAP_MB)
                time.sleep(1)
        
        # Stop (60%)
        show_progress(60)
        shell.send("wbox-cli debug close pcap\n")
        time.sleep(0.3)
        shell.recv(4096)
        shell.send("wbox-cli debug dropped_packets disable\n")
        time.sleep(0.3)
        shell.recv(4096)
        
        # Wait for file
        print(f"\nWaiting for file...")
        time.sleep(3)
        
        # Verify (65%)
        show_progress(65)
        shell.send(f"ls -la /tmp/{pcap_filename}\n")
        time.sleep(0.5)
        verify_output = shell.recv(4096).decode()
        
        if "No such file" in verify_output:
            print(f"\nFile not created!")
            ssh.close()
            return
        
        # Namespace switch (70%) - Use correct namespace based on deployment
        show_progress(70)
        if config.get('deployment') == 'cluster':
            shell.send("ip netns exec oob_ncc_ns bash\n")
        else:
            shell.send("ip netns exec oob_ns bash\n")
        time.sleep(0.3)
        shell.recv(4096)
        
        # SCP (80%) — atomic upload+delete: device-side pcap is removed only if
        # the scp succeeded. Prevents leftover /tmp/*.pcap from filling the
        # partition when the script is interrupted between upload and cleanup.
        show_progress(80)
        scp_cmd = (f"scp /tmp/{pcap_filename} "
                   f"{config['server_user']}@{config['server_host']}:{config['server_path']}/ "
                   f"&& rm -f /tmp/{pcap_filename}")
        shell.send(scp_cmd + '\n')
        time.sleep(0.3)
        
        # Upload monitoring (90%)
        show_progress(90)
        upload_success = False
        upload_start_time = time.time()
        max_upload_time = 30
        
        while time.time() - upload_start_time < max_upload_time:
            if shell.recv_ready():
                chunk = shell.recv(2048).decode()
                
                if "password:" in chunk.lower() or "Password:" in chunk:
                    shell.send(f"{config['server_pass']}\n")
                
                if "100%" in chunk:
                    upload_success = True
                    show_progress(95)
                    break
                    
                if "No such file" in chunk or "denied" in chunk.lower() or "failed" in chunk.lower():
                    break
                    
                if "#" in chunk and ("oob_n" in chunk or "root@" in chunk):
                    upload_success = True
                    show_progress(95)
                    break
            else:
                time.sleep(0.2)
        
        if not upload_success and time.time() - upload_start_time >= max_upload_time:
            show_progress(95)
            upload_success = True
        
        # Complete (100%)
        show_progress(100)
        
        if upload_success:
            print(f"\nSUCCESS! Capture uploaded to server!")
            print(f"File: {pcap_filename}")
            
            # Download
            print("Downloading to PC...")
            download_success = fast_download(pcap_filename, config)
            
            if not download_success:
                print("Retrying download...")
                time.sleep(2)
                download_success = fast_download(pcap_filename, config)
            
            if download_success:
                downloads_path = get_downloads_folder()
                local_file = f"{downloads_path}/{pcap_filename}"
                
                print(f"Opening Wireshark...")
                
                import platform
                wireshark_opened = False
                try:
                    if platform.system() == "Darwin":
                        subprocess.run(f'open -a Wireshark "{local_file}"', shell=True, check=True)
                        wireshark_opened = True
                except:
                    pass
                
                if wireshark_opened:
                    print("Wireshark opened!")
                    print("Cleaning up device (belt-and-braces)...")
                    shell.send(f"rm -f /tmp/{pcap_filename}\n")
                    time.sleep(0.1)
                    shell.recv(4096)

                    shell.close()
                    ssh.close()

                    # Server-side file is auto-removed by rsync --remove-source-files
                    # in fast_download(); fast_cleanup_server is now a no-op fallback
                    # for cases where rsync was unavailable and we fell back to scp.
                    fast_cleanup_server(pcap_filename, config)
                    print("COMPLETE!")
                    return
                else:
                    print("Files kept for manual retrieval")
            else:
                print("Download failed - files on server")
        else:
            print(f"\nUpload may have failed")
        
        try:
            shell.close()
            ssh.close()
        except:
            pass
        
    except KeyboardInterrupt:
        pf = locals().get('pcap_filename', '')
        sh = locals().get('shell', None)
        sc = locals().get('ssh', None)
        print(f"\nInterrupted!" + (f" Cleaning up partial /tmp/{pf} on the device..." if pf else ""))
        try:
            if sh is not None:
                sh.send("wbox-cli debug close pcap\n")
                time.sleep(0.3)
                sh.recv(4096)
                sh.send("wbox-cli debug dropped_packets disable\n")
                time.sleep(0.3)
                sh.recv(4096)
                if pf:
                    # Remove the half-written pcap so it does not accumulate in /tmp.
                    sh.send(f"rm -f /tmp/{pf}\n")
                    time.sleep(0.3)
                    sh.recv(4096)
                    # Belt-and-braces sweep — nuke any stray *.pcap from prior crashed runs too.
                    sh.send("rm -f /tmp/*.pcap\n")
                    time.sleep(0.3)
                    sh.recv(4096)
                    print(f"Removed /tmp/{pf} (and any other leftover *.pcap)")
                sh.close()
            if sc is not None:
                sc.close()
        except Exception as e:
            print(f"(cleanup best-effort failed: {e}; next run will sweep leftovers)")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

def routing_engine_capture(device_host, device_user, device_pass):
    """Routing engine capture (original RE code)"""
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python3 dn_capture.py [filename_prefix] [duration_seconds]")
        print("Examples:")
        print("  python3 dn_capture.py 10           -> 10s capture, prefix='capture'")
        print("  python3 dn_capture.py test         -> infinite capture, prefix='test'")
        print("  python3 dn_capture.py test 30      -> 30s capture, prefix='test'")
        print("  python3 dn_capture.py test inf     -> infinite capture, prefix='test'")
        sys.exit(1)

    INF_TOKENS = ('inf', 'infinite', 'forever', '0')

    if len(sys.argv) == 2:
        a = sys.argv[1].lower()
        if a in INF_TOKENS:
            filename_prefix = "capture"
            duration = None
            print("\nInfinite capture mode - press Ctrl+C to stop")
        elif a.isdigit():
            filename_prefix = "capture"
            duration = int(a)
            print(f"\nDuration: {duration} seconds (auto prefix='capture')")
        else:
            filename_prefix = sys.argv[1]
            duration = None
            print("\nInfinite capture mode - press Ctrl+C to stop")
    else:
        filename_prefix = sys.argv[1]
        duration_str = sys.argv[2].lower()
        if duration_str in INF_TOKENS:
            duration = None
            print("\nInfinite capture mode - press Ctrl+C to stop")
        else:
            duration = int(duration_str)
    
    config = {
        'mode': 'routing',
        'deployment': 'standalone',
        'ncp': None,
        'device_host': device_host,
        'device_user': device_user,
        'device_pass': device_pass,
        'server_host': "10.10.75.210",
        'server_user': "dn",
        'server_pass': "Drive1234!",
        'server_path': "/home/dn"
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pcap_filename = f"{filename_prefix}_{timestamp}.pcap"
    
    print(f"\nCapture file: {pcap_filename}")
    if duration:
        print(f"Duration: {duration} seconds")
    else:
        print(f"Duration: Infinite (Ctrl+C to stop)")
    print()
    
    try:
        # Connect
        print("Connecting...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['device_host'], username=config['device_user'], 
                   password=config['device_pass'], timeout=15)
        
        print("Opening shell...")
        shell = ssh.invoke_shell()
        time.sleep(0.5)
        if shell.recv_ready():
            shell.recv(8192)
        
        def wait_for(prompts, timeout=10):
            buf = ""
            end_time = time.time() + timeout
            while time.time() < end_time:
                if shell.recv_ready():
                    chunk = shell.recv(4096).decode(errors="ignore")
                    buf += chunk
                    if any(p in buf for p in prompts):
                        return buf
                time.sleep(0.1)
            return buf
        
        # Enter routing engine shell
        print("Entering routing engine...")
        shell.send("run start shell\n")
        output = wait_for(["Password:", "root@routing_engine"], 10)
        
        if "Password:" in output:
            shell.send(f"{config['device_pass']}\n")
            output = wait_for(["root@routing_engine", "inband_ns"], 10)
        
        if "root@routing_engine" not in output:
            print("ERROR: Failed to enter routing engine")
            ssh.close()
            return
        
        print("✓ Entered routing engine")
        
        # Find container
        print("Finding container...")
        shell.send("docker ps | grep routing-engine\n")
        time.sleep(1)
        docker_output = ""
        
        while shell.recv_ready():
            docker_output += shell.recv(4096).decode(errors='ignore')
            time.sleep(0.1)
        
        pattern = f"{config['device_host']}_routing-engine\\.[a-z0-9]+\\.[a-z0-9]+"
        match = re.search(pattern, docker_output)
        
        if match:
            container_name = match.group(0)
            print(f"Container: {container_name}")
        else:
            print(f"ERROR: Container not found")
            ssh.close()
            return
        
        # Cleanup
        print("Cleanup...")
        shell.send("rm -f /tmp/*.pcap\n")
        time.sleep(0.3)
        shell.recv(8192)
        
        # Start tcpdump
        print("Starting capture...")
        
        if duration:
            tcpdump_cmd = f"docker exec {container_name} ip netns exec inband_ns timeout {duration} tcpdump -nqe -i any -w /tmp/{pcap_filename}\n"
        else:
            tcpdump_cmd = f"docker exec {container_name} ip netns exec inband_ns tcpdump -nqe -i any -w /tmp/{pcap_filename}\n"
        
        shell.send(tcpdump_cmd)
        time.sleep(1)
        output = wait_for(["listening on", "packets captured"], 3)
        
        if "listening on" not in output.lower():
            print("ERROR: Tcpdump failed")
            ssh.close()
            return
        
        print("Capture started!")
        
        # Capture duration
        if duration is None:
            print(f"\nPress Ctrl+C to stop...")
            start_time = time.time()
            
            try:
                while True:
                    elapsed = int(time.time() - start_time)
                    print(f"\rCapturing... {elapsed}s", end='', flush=True)
                    time.sleep(1)
            except KeyboardInterrupt:
                print(f"\nStopping...")
                shell.send('\x03')
                time.sleep(2)
        else:
            print(f"\nCapturing for {duration}s...")
            for sec in range(duration):
                print(f"\rProgress: {sec+1}/{duration}s", end='', flush=True)
                time.sleep(1)
            print()
        
        # Wait for file
        print("Waiting for file...")
        time.sleep(3)
        
        # Verify
        print("Verifying...")
        shell.send(f"ls -lh /tmp/{pcap_filename}\n")
        time.sleep(1)
        verify_output = ""
        while shell.recv_ready():
            verify_output += shell.recv(4096).decode(errors='ignore')
            time.sleep(0.1)
        
        if "No such file" in verify_output:
            print("ERROR: File not created!")
            ssh.close()
            return
        
        # OOB namespace
        print("Switching namespace...")
        shell.send("ip netns exec oob_ncc_ns bash\n")
        time.sleep(0.5)
        shell.recv(8192)
        
        # Transfer
        print("Transferring...")
        scp_cmd = f'scp /tmp/{pcap_filename} {config["server_user"]}@{config["server_host"]}:{config["server_path"]}/{pcap_filename}'
        shell.send(scp_cmd + '\n')
        time.sleep(0.5)
        
        upload_success = False
        start_time = time.time()
        
        while time.time() - start_time < 60:
            if shell.recv_ready():
                chunk = shell.recv(4096).decode(errors='ignore')
                
                if "password:" in chunk.lower():
                    shell.send(f"{config['server_pass']}\n")
                
                if "100%" in chunk or "#" in chunk:
                    upload_success = True
                    break
            else:
                time.sleep(0.2)
        
        if not upload_success:
            print("ERROR: Upload failed")
            ssh.close()
            return
        
        print("Upload complete!")
        
        # Cleanup device
        shell.send(f"rm -f /tmp/{pcap_filename}\n")
        time.sleep(0.3)
        shell.close()
        ssh.close()
        
        # Download
        print("Downloading to Mac...")
        
        downloads_path = os.path.expanduser("~/Downloads")
        local_file = f"{downloads_path}/{pcap_filename}"
        
        scp_dl = f'scp {config["server_user"]}@{config["server_host"]}:{config["server_path"]}/{pcap_filename} "{downloads_path}/"'
        
        # Try expect
        expect_script = f'''
expect -c "
set timeout 60
spawn {scp_dl}
expect {{
    \\"password:\\" {{ send \\"{config["server_pass"]}\\r\\"; exp_continue }}
    eof
}}
"
'''
        subprocess.run(expect_script, shell=True, capture_output=True, timeout=90)
        
        if os.path.exists(local_file) and os.path.getsize(local_file) > 24:
            print(f"Downloaded!")
            
            # Open Wireshark
            print("Opening Wireshark...")
            
            import platform
            if platform.system() == "Darwin":
                subprocess.run(f'open -a Wireshark "{local_file}"', shell=True)
            
            print(f"\nSUCCESS! Wireshark opened")
            
            # Cleanup server
            print("Cleaning up...")
            cleanup_script = f'''
expect -c "
set timeout 30
spawn ssh {config['server_user']}@{config['server_host']} rm -f {config['server_path']}/{pcap_filename}
expect {{
    \\"password:\\" {{ send \\"{config['server_pass']}\\r\\"; exp_continue }}
    eof
}}
"
'''
            subprocess.run(cleanup_script, shell=True, capture_output=True, timeout=45)
            print("Done!")
        else:
            print("ERROR: Download failed")
            
    except KeyboardInterrupt:
        pf = locals().get('pcap_filename', '')
        cn = locals().get('container_name', '')
        sh = locals().get('shell', None)
        sc = locals().get('ssh', None)
        print(f"\nInterrupted!" + (f" Cleaning up partial /tmp/{pf} in container..." if pf else ""))
        try:
            if sh is not None:
                sh.send('\x03')
                time.sleep(1)
                if pf and cn:
                    try:
                        sh.send(f"docker exec {cn} rm -f /tmp/{pf}\n")
                        time.sleep(0.5)
                        sh.recv(4096)
                        print(f"Removed /tmp/{pf} from {cn}")
                    except Exception:
                        pass
                sh.close()
            if sc is not None:
                sc.close()
        except Exception as e:
            print(f"(cleanup best-effort failed: {e})")
        sys.exit(0)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    _check_for_update()
    last_settings = load_saved_settings()
    
    device_host = None
    device_user = None
    device_pass = None
    mode = None
    deployment = None
    ncp = None
    
    if last_settings:
        # Get values from last settings
        device_host = last_settings.get('device_host') or last_settings.get('hostname')
        device_user = last_settings.get('device_user') or last_settings.get('username')
        device_pass = last_settings.get('device_pass') or last_settings.get('password')
        mode = last_settings.get('mode', 'datapath')
        deployment = last_settings.get('deployment', 'standalone')
        ncp = last_settings.get('ncp', '0')
        
        # Ask if using last settings
        print("DriveNets Fast Capture")
        print("=" * 25)
        print(f"\nLast used:")
        print(f"  {device_user}@{device_host}")
        print(f"  Type: {deployment.upper()}")
        print(f"  Target: {mode.upper()}")
        if deployment == 'cluster' and mode == 'datapath':
            print(f"  NCP: {ncp}")
        
        choice = input(f"Use these settings? (y/n): ").strip().lower()
        
        if choice == 'y' or choice == 'yes' or choice == '':
            # Using saved settings - verify system type and NCP in case they changed
            print("\nVerifying system configuration...")
            try:
                temp_ssh = paramiko.SSHClient()
                temp_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                temp_ssh.connect(device_host, username=device_user, password=device_pass, timeout=10)
                
                # Re-detect deployment type
                detected_deployment = detect_deployment_type(temp_ssh)
                if detected_deployment and detected_deployment != deployment:
                    print(f"⚠ System type changed from {deployment.upper()} to {detected_deployment.upper()}")
                    deployment = detected_deployment
                
                # Re-detect NCP if cluster+datapath
                if deployment == 'cluster' and mode == 'datapath':
                    detected_ncp = detect_ncp_from_port_mirroring(temp_ssh)
                    
                    if detected_ncp and detected_ncp != ncp:
                        print(f"⚠ NCP changed from {ncp} to {detected_ncp}")
                        ncp = detected_ncp
                    elif detected_ncp:
                        print(f"✓ NCP confirmed: {ncp}")
                    else:
                        print(f"⚠ Could not verify NCP, using saved: {ncp}")
                
                temp_ssh.close()
                
                # Update saved settings if anything changed
                save_settings(mode, deployment, ncp, device_host, device_user, device_pass)
                
            except Exception as e:
                print(f"⚠ Verification failed: {e}, using saved settings")
        else:
            device_host = None  # Reset to ask for new settings
    
    if device_host is None:
        # Ask for new settings
        if not last_settings:
            print("DriveNets Fast Capture")
            print("=" * 25)
        
        # Get device credentials FIRST
        device_host = input("\nDevice hostname/IP: ").strip() or "YC71F5VJ00008P2"
        device_user = input("Device username [dnroot]: ").strip() or "dnroot"
        device_pass = getpass.getpass("Device password: ") or "dnroot"
        
        # AUTO-DETECT deployment type (Standalone vs Cluster)
        print("\nConnecting to device to detect system type...")
        try:
            temp_ssh = paramiko.SSHClient()
            temp_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            temp_ssh.connect(device_host, username=device_user, password=device_pass, timeout=10)
            
            deployment = detect_deployment_type(temp_ssh)
            
            # Auto-detect NCP if cluster
            if deployment == 'cluster':
                ncp = detect_ncp_from_port_mirroring(temp_ssh)
                if not ncp:
                    print("\nNCP auto-detection failed. Please enter manually.")
                    ncp = input("Which NCP number? (e.g., 0, 1, 2, 6, 18): ").strip() or "0"
            
            temp_ssh.close()
            
            if not deployment:
                print("\n⚠ Could not auto-detect system type")
                print("Deployment type:")
                print("  1. Standalone")
                print("  2. Cluster")
                deployment_choice = input("Select (1=Standalone, 2=Cluster): ").strip()
                deployment = 'cluster' if deployment_choice == '2' else 'standalone'
                
                if deployment == 'cluster':
                    ncp = input("Which NCP number? (e.g., 0, 1, 2, 6, 18): ").strip() or "0"
            
        except Exception as e:
            print(f"\n⚠ Auto-detection failed: {e}")
            print("Deployment type:")
            print("  1. Standalone")
            print("  2. Cluster")
            deployment_choice = input("Select (1=Standalone, 2=Cluster): ").strip()
            deployment = 'cluster' if deployment_choice == '2' else 'standalone'
            
            if deployment == 'cluster':
                ncp = input("Which NCP number? (e.g., 0, 1, 2, 6, 18): ").strip() or "0"
        
        # Ask Datapath or Routing Engine
        print("\nCapture from:")
        print("  1. Datapath")
        print("  2. Routing Engine")
        mode_choice = input("Select (1=Datapath, 2=Routing Engine): ").strip()
        mode = 'routing' if mode_choice == '2' else 'datapath'
        
        # Set NCP if not already set
        if mode == 'datapath' and deployment == 'standalone':
            ncp = '0'
        elif mode != 'datapath':
            ncp = None
        
        # Save settings
        save_settings(mode, deployment, ncp, device_host, device_user, device_pass)
    
    # Call appropriate capture function with credentials
    if mode == 'routing':
        # Routing engine (works same for both standalone and cluster)
        routing_engine_capture(device_host, device_user, device_pass)
    else:
        # Datapath (standalone with NCP 0, or cluster with auto-detected NCP)
        datapath_capture(device_host, device_user, device_pass, deployment, ncp)
