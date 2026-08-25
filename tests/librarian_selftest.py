"""librarian_selftest — the planted-claim gate the Librarian must be seen to refuse.

Invoked by ls-librarian's Step 2.5 and runnable standalone:

    python tests/librarian_selftest.py            # the gate (exit 0 = refused all plants)
    python tests/librarian_selftest.py --invert   # negative proof: MUST exit 1

Plants THREE claims in a temp note and runs them through the REAL
librarian_checks module (the same code the Vault Scout consumes — a scripted
twin would drift):

  1. a sha that does not exist (0-padded), repo-qualified  -> must be REFUTED
  2. a `file:line` assertion about a missing file          -> must be REFUTED
  3. a FALSE BEHAVIORAL claim on a REAL sha, unattested    -> structural half
     verified, behavioral half must land awaiting-human — a passing existence
     proof must never launder the prose

Exit 1 if anything false is promoted. `--invert` flips the verdict so the
gate itself can be seen RED — a gate never seen red is not a gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liteharness.librarian_checks import check_notes  # noqa: E402


def _make_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "module.ts").write_text("export const x = 1;\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "module.ts"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=selftest",
         "-c", "user.email=selftest@example.com", "commit", "-q", "-m", "init"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def run_gate() -> tuple[bool, list[str]]:
    """Returns (all_plants_refused, verdict_lines)."""
    verdict: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "planted-repo"
        real_sha = _make_repo(repo)
        anchor = repo / "module.ts"

        fake_sha = "0" * 40
        note = base / "2099-12-31.md"
        note.write_text(
            f"deployed {fake_sha} touching {anchor}\n"
            f"regression traced to `{repo / 'does-not-exist.ts'}:42`\n"
            f"{real_sha} fixed the wake VAD, see {anchor}\n",
            encoding="utf-8",
        )

        result = check_notes(str(base / "*.md"), days=None)
        by_line = {c["line"]: c for c in result["claims"]}

        ok = True

        c1 = by_line.get(1)
        if c1 and c1["status"] == "refuted":
            verdict.append("PLANT 1 (nonexistent sha): REFUTED — correct")
        else:
            ok = False
            verdict.append(
                f"PLANT 1 (nonexistent sha): {c1['status'] if c1 else 'MISSING'} — "
                "a fabricated sha survived the sieve"
            )

        c2 = by_line.get(2)
        if c2 and c2["status"] == "refuted":
            verdict.append("PLANT 2 (missing file:line): REFUTED — correct")
        else:
            ok = False
            verdict.append(
                f"PLANT 2 (missing file:line): {c2['status'] if c2 else 'MISSING'} — "
                "a dead path survived the sieve"
            )

        c3 = by_line.get(3)
        if (
            c3
            and c3["status"] == "verified"
            and c3.get("behavioral", {}).get("status") == "awaiting-human"
        ):
            verdict.append(
                "PLANT 3 (false behavioral on real sha): structural verified, "
                "behavioral awaiting-human — NOT promoted, correct"
            )
        else:
            ok = False
            behavioral = c3.get("behavioral", {}).get("status") if c3 else "MISSING"
            verdict.append(
                f"PLANT 3 (false behavioral on real sha): structural="
                f"{c3['status'] if c3 else 'MISSING'} behavioral={behavioral} — "
                "an existence proof laundered a behavioral claim"
            )

        return ok, verdict


def main() -> int:
    invert = "--invert" in sys.argv
    ok, verdict = run_gate()
    for line in verdict:
        print(line)

    if invert:
        # Negative proof: pretend the plants had to be PROMOTED. Against a
        # correct checker this MUST fail — proving the gate can go red.
        ok = not ok
        print("[--invert] verdict flipped for the negative proof")

    if ok:
        print("SELFTEST OK: all three planted claims were refused")
        return 0
    print("SELFTEST FAILED: something false was promoted (or --invert ran "
          "against a correct checker, which is the point)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
