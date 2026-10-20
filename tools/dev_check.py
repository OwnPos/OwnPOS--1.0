"""One command that must pass before any change is released:
    python tools/dev_check.py "host=localhost port=5432 user=postgres password=..."
Runs the full test suite and the program self-test (every screen opens on a throw-away database) against that
PostgreSQL server, and checks that the version, changelog and packaging files agree. Exit code 0 = good."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], env=None) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT, env=env)


def main() -> int:
    pg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OWNPOS_TEST_PG", "")
    if not pg:
        print('Give the test PostgreSQL server: python tools/dev_check.py "host=... port=... user=... password=..."')
        return 2
    problems = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(ROOT), OWNPOS_TEST_PG=pg)
    if run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests_pg"], env) != 0:
        problems.append("tests failed")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "selftest.txt"
        env2 = dict(env, OWNPOS_DATA=str(Path(tmp) / "data"), OWNPOS_SELFTEST_PG=pg)
        code = run([sys.executable, "launcher.py", "--self-test", str(report)], env2)
        text = report.read_text(encoding="utf-8") if report.exists() else "no report"
        print(text.splitlines()[0] if text else "")
        if code != 0:
            problems.append("self-test failed:\n" + text)
    version = re.search(r'APP_VERSION = "([^"]+)"', (ROOT / "ownpos" / "__init__.py").read_text()).group(1)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version}" not in changelog:
        problems.append(f"CHANGELOG.md has no section for version {version}")
    viss = ROOT / "installer" / "version.iss"
    if not viss.exists() or f'"{version}"' not in viss.read_text(encoding="utf-8"):
        problems.append("installer/version.iss does not match the version: run  python tools/write_version.py")
    for f in ("ownpos.spec", "installer/OwnPOS.iss", "tools/make_installer.ps1", "requirements.txt",
              "docs/SETUP.md", "ownpos/docs/user_guide.md"):
        if not (ROOT / f).exists():
            problems.append(f"missing {f}")
    if "Password={#InstallPassword}" not in (ROOT / "installer" / "OwnPOS.iss").read_text(encoding="utf-8"):
        problems.append("installer/OwnPOS.iss lost its password protection")
    for bat in ROOT.glob("*.bat"):
        if b"\r\n" not in bat.read_bytes():
            problems.append(f"{bat.name} must have Windows (CRLF) line endings")
    if (ROOT / "devpos").exists():
        problems.append("the old devpos/ reference folder is still in the project")
    if problems:
        print("\nDEV CHECK FAILED:\n- " + "\n- ".join(problems))
        return 1
    print(f"\nDEV CHECK OK (version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
