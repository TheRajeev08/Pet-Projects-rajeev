"""Create an isolated run directory for one FY27 Territory Plan build.

Every invocation gets its own folder under ~/.copilot/fy27-territory-plan/runs/, so no
teammate's CRM data is ever written back into the shared plugin package and two runs
never overwrite each other. The uploaded workbook is copied in verbatim; a UTF-8 round
trip silently corrupts XLSX bytes, so this must stay a binary copy.

    python3 new_run.py "<path to SuperDash export>"
    -> {"runDir": ..., "inputPath": ..., "sourceName": ...}
"""
import datetime
import json
import os
import shutil
import sys

RUNS_ROOT = os.path.join(os.path.expanduser("~"), ".copilot", "fy27-territory-plan", "runs")


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: new_run.py "<path to SuperDash export>"')
    source = os.path.expanduser(sys.argv[1])
    if not os.path.isfile(source):
        raise SystemExit(f"No such file: {source}")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(RUNS_ROOT, stamp)
    os.makedirs(run_dir, exist_ok=True)

    suffix = os.path.splitext(source)[1].lower() or ".bin"
    input_path = os.path.join(run_dir, f"uploaded-territory{suffix}")
    shutil.copyfile(source, input_path)

    print(
        json.dumps(
            {
                "runDir": run_dir,
                "inputPath": input_path,
                "sourceName": os.path.basename(source),
            }
        )
    )


if __name__ == "__main__":
    main()
