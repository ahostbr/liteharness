"""Identity-free launcher refusal is independent of files, PATH and config."""
import pytest

from liteharness import deny_floor


@pytest.mark.parametrize("command", [
    "run", "run.bat", "./run.bat", "./missing/run.bat",
    "cmd /c run", "env X=1 nohup ./run.bat", "timeout 5 ./run.bat",
    "cd missing-folder; run", "Start-Process -FilePath ./run.bat",
    'python -c "from subprocess import run; run(cmd)"',
])
@pytest.mark.parametrize("claim", [None, "", "relative/run.bat", "[malformed]", "self"])
def test_identity_claims_never_hide_recognized_launcher(tmp_path, monkeypatch, command, claim):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT")
    # A preceding executable used to prevent identification of the .bat file.
    (tmp_path / "run.exe").write_bytes(b"inert")
    if claim is None:
        monkeypatch.delenv("LITETUI_OWNER_LAUNCHER_PATH", raising=False)
    else:
        monkeypatch.setenv("LITETUI_OWNER_LAUNCHER_PATH", claim)
    reason = deny_floor.refusal(command, tmp_path, shell="powershell")
    assert reason and "[owner-launcher]" in reason


@pytest.mark.parametrize("exists", [False, True])
def test_other_absolute_launcher_is_conservative_overblock(tmp_path, exists):
    other = tmp_path / "other" / "run.bat"
    if exists:
        other.parent.mkdir()
        other.write_bytes(b"inert")
    reason = deny_floor.refusal(f'& "{other}"', tmp_path, shell="powershell")
    assert reason and "[owner-launcher]" in reason


@pytest.mark.parametrize("command", [
    "Get-Content ./run.bat", "echo run.bat", '$x = "run"',
    'return "run"', "@'\nrun\n'@ | Add-Content fixture.txt",
])
def test_existing_reader_and_proven_data_guards_remain(tmp_path, command):
    assert deny_floor.refusal(command, tmp_path, shell="powershell") is None
