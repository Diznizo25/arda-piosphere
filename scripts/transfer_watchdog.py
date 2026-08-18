"""
Watchdog supervisor for scripts/transfer_assets_to_r2.py.

Keeps the GEE -> R2 asset transfer running in the background CONTINUOUSLY until
it actually finishes. The transfer script alone is resumable (persistent tile
cache) but a single invocation can be killed by the flaky GEE download
connection, a reaped background terminal, a network drop, or a hang. This
supervisor:

  1. launches scripts/transfer_assets_to_r2.py as a subprocess
  2. if the subprocess exits WITHOUT printing the final "Done." summary,
     waits and relaunches it (it resumes from the persistent tile cache)
  3. if the subprocess is still alive but its output has not grown for
     STALE_AFTER_S (default 25 min), terminates it and relaunches (hung
     download guard -- a single tile request can legitimately block up to
     10 min before the requests timeout, so 25 min is generous)
  4. exits only once a "Done." summary appears in the captured output

Run it detached so it survives the launching terminal:
  Start-Process -FilePath python -ArgumentList 'scripts/transfer_watchdog.py' -WindowStyle Hidden

Logs:
  transfer_watchdog.log                 - watchdog lifecycle events
  transfer_watchdog_transfer.log        - captured transfer output (appended
                                          across restarts; completion is
                                          detected from this file)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSFER_SCRIPT = ROOT / "scripts" / "transfer_assets_to_r2.py"
OUTPUT_LOG = ROOT / "transfer_watchdog_transfer.log"
LIFECYCLE_LOG = ROOT / "transfer_watchdog.log"

# A single tile download can block up to 600s before requests raises Timeout,
# plus retry backoff sleeps (up to ~165s). Multi-part uploads of ~500MB COGs can
# legitimately stream for well over an hour on a slow link; the transfer logs a
# heartbeat every 60s during uploads, so in practice output keeps flowing. 3
# hours is a generous backstop that only trips on a genuinely hung subprocess.
STALE_AFTER_S = 3 * 60 * 60
POLL_INTERVAL_S = 60
RESTART_DELAY_S = 20
FAST_CRASH_BACKOFF_S = 120
FAST_CRASH_COUNT = 5
DONE_RE = re.compile(r"^.*Done\.\s+\d+\s+transferred.*$", re.MULTILINE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LIFECYCLE_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("transfer_watchdog")


def _run_count() -> int:
    """Number of transfer invocations so far (inferred from output log headers)."""
    if not OUTPUT_LOG.exists():
        return 0
    text = OUTPUT_LOG.read_text(encoding="utf-8", errors="replace")
    return text.count("=== transfer_assets_to_r2.py invocation")


def _is_done() -> bool:
    if not OUTPUT_LOG.exists():
        return False
    text = OUTPUT_LOG.read_text(encoding="utf-8", errors="replace")
    return bool(DONE_RE.search(text))


def _output_size() -> int:
    try:
        return OUTPUT_LOG.stat().st_size
    except FileNotFoundError:
        return 0


def main() -> int:
    log.info(f"Watchdog started (pid={os.getpid()}, python={sys.executable})")
    log.info(f"Transfer script: {TRANSFER_SCRIPT}")
    log.info(f"Stale threshold: {STALE_AFTER_S}s; poll: {POLL_INTERVAL_S}s")

    fast_crashes = 0
    while True:
        if _is_done():
            log.info("Transfer already complete (Done. found in output log). Exiting.")
            return 0

        run_no = _run_count() + 1
        log.info(f"Launching transfer invocation #{run_no}")
        with open(OUTPUT_LOG, "a", encoding="utf-8") as out:
            out.write(
                f"\n===== {datetime.now().isoformat(timespec='seconds')} "
                f"=== transfer_assets_to_r2.py invocation #{run_no} "
                f"(python={sys.executable}) =====\n"
            )
            out.flush()
            proc = subprocess.Popen(
                [sys.executable, str(TRANSFER_SCRIPT)]
                + (["--force"] if "--force" in sys.argv[1:] else []),
                cwd=str(ROOT),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        size_at_start = _output_size()
        prev_size = size_at_start
        started = time.time()
        last_growth = time.time()

        while True:
            time.sleep(POLL_INTERVAL_S)
            if _is_done():
                log.info("Transfer complete (Done. found). Watchdog exiting.")
                return 0

            still_running = proc.poll() is None
            if still_running:
                # Hung guard: no output-size change for a long time -> kill + restart.
                cur_size = _output_size()
                if cur_size != prev_size:
                    prev_size = cur_size
                    last_growth = time.time()
                if time.time() - last_growth > STALE_AFTER_S:
                    log.warning(
                        f"No output for {STALE_AFTER_S}s; terminating hung "
                        f"transfer (pid={proc.pid}) and restarting."
                    )
                    proc.kill()
                    proc.wait(timeout=30)
                    still_running = False
                else:
                    continue

            # Subprocess has exited.
            elapsed = time.time() - started
            code = proc.returncode
            log.info(f"Transfer invocation #{run_no} exited with code {code} after {elapsed:.0f}s")
            if _is_done():
                log.info("Done. detected after exit. Watchdog exiting.")
                return 0

            if elapsed < 15:
                fast_crashes += 1
            else:
                fast_crashes = 0
            delay = FAST_CRASH_BACKOFF_S if fast_crashes >= FAST_CRASH_COUNT else RESTART_DELAY_S
            log.warning(
                f"Transfer did not complete; restarting in {delay}s "
                f"(fast_crash_count={fast_crashes})."
            )
            time.sleep(delay)
            break  # outer loop relaunches


if __name__ == "__main__":
    sys.exit(main())

