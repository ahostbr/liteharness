"""T0291-A-A: literal DENY positions, never execute any launcher or deletion."""
import pytest

from liteharness import deny_floor, deny_gate

D3_LAUNCHES = [
    "<# c #> {launch}", "<# c #><# second #>{launch}",
    "$x = {launch}", "$x={launch}", "return {launch}",
    "Start-Process -Wait {launch}", "Start-Process -NoNewWindow {launch}",
    "Start-Process -PassThru -FilePath {launch}", "saps {launch}",
    "saps -Wait -FilePath:{launch}",
    "nohup {launch}", "time {launch}", "time -p {launch}",
    "timeout 5 {launch}", "timeout --signal=TERM 5s {launch}",
    "exec {launch}", "command {launch}", "env X=1 {launch}",
    "env -i X=1 {launch}", "X=1 {launch}",
    "cmd /v:on /c {launch}", "cmd /e:on /c {launch}",
    "env X=1 nohup {launch}",
]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    launcher = tmp_path / "owner" / "run.bat"
    launcher.parent.mkdir()
    launcher.write_text("@echo off\n", encoding="utf-8")
    # Released identity-free policy has no _OWNER_LAUNCHER override. The file
    # supplies literal command spellings only; existence is not an exemption.
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    return launcher


@pytest.mark.parametrize("shell", [None, "powershell", "bash", "cmd"])
@pytest.mark.parametrize("template, launch", [
    (template, launch) for template in D3_LAUNCHES
    for launch in ["run", "./run.bat", '"{owner}"']
    if not (launch.startswith('"') and template.startswith(("$x", "return")))
])
def test_d3_literal_owner_positions(owner, shell, launch, template):
    command = template.format(launch=launch.format(owner=owner))
    reason = deny_floor.refusal(command, owner.parent, shell=shell, jobs=False)
    assert reason and "[owner-launcher]" in reason


@pytest.mark.parametrize("template", D3_LAUNCHES)
def test_d3_denies_other_and_missing_launcher_identity(owner, tmp_path, template):
    other = tmp_path / "other"
    other.mkdir()
    (other / "run.bat").write_text("@echo off\n", encoding="utf-8")
    command = template.format(launch="./run.bat")
    assert "[owner-launcher]" in deny_floor.refusal(command, other, jobs=False)
    owner.unlink()  # Inert temporary fixture only; nothing is ever executed.
    assert "[owner-launcher]" in deny_floor.refusal(command, owner.parent, jobs=False)


@pytest.mark.parametrize("command", [
    '<# c #> Get-Content run.bat', '<# c #> echo run',
    '$x = Get-Content run.bat', '$x=echo run', '$x = "run"',
    'return Get-Content run.bat', 'return "run"',
    'Write-Output "<# c #> run"', 'echo "$x = run"',
    'echo "Start-Process -Wait run"', 'echo "nohup run"',
    'echo "env X=1 run"', '# run', 'Get-Date # run',
    'nohup cat run.bat', 'env X=1 cat run.bat', 'X=1 cat run.bat',
    'sudo cat run.bat', 'time cat run.bat', 'timeout 5 cat run.bat',
    'command -v run', 'command -V run', 'env run=1',
    'Start-Process other.exe -ArgumentList run', 'saps other.exe run',
    'Start-Process -WorkingDirectory run', 'sudo -u run cat file',
    'env -u run cat file', 'timeout run cat file',
    'npm run build', 'uv run python -V', 'python script.py run',
    '<# run #> Get-Date', '<# run #> Get-Content run.bat',
    '$x = \'run\'', 'return \'run\'', '$x = "./run.bat"',
    'env -u run', 'timeout --signal=run 5 cat file',
    'Start-Process -WindowStyle run', 'saps -ArgumentList run',
    'Start-Process -FilePath -Wait run', '/v:on run',
    'env --help run', 'exec -a run cat file', 'time --output run cat file',
])
def test_d3_benign_arguments_and_quoted_data(owner, command):
    assert deny_floor.refusal(command, owner.parent, shell="powershell", jobs=False) is None


@pytest.mark.parametrize("shell", [None, "bash", "powershell", "cmd"])
@pytest.mark.parametrize("command, refused", [
    ("env X=run cat file", False), ("X=run", False),
    ("env X=./run.bat cat file", False), ("X=run cat file", False),
    ("env X=run", False), ("env X=run.bat cat file", False),
    ("X=./run.bat", False), ("env X=1 run", True), ("X=1 run", True),
    ("env X=run run", True), ("X=run ./run.bat", True),
    ("env X=run.bat ./run.bat", True),
    ("env X=run Y=./run.bat cat file", False),
    ("X=run Y=./run.bat", False), ("X='run' cat file", False),
    ('env X="run.bat" cat file', False),
    ("env X=run Y=./run.bat run", True),
    ("X=run Y=./run.bat ./run.bat", True),
    ("env X=run cat file; ./run.bat", True),
    ("X=run; run", True),
])
def test_d3_environment_assignment_value_is_not_argv0(owner, command, refused, shell):
    reason = deny_floor.refusal(command, owner.parent, shell=shell, jobs=False)
    assert bool(reason) is refused
    if refused:
        assert "[owner-launcher]" in reason


def test_d3_gate_routes_remaining_floor(owner, monkeypatch):
    monkeypatch.setattr(deny_gate, "_load_floor", lambda: deny_floor)
    payload = {"tool_name": "Bash", "tool_input": {"command": "Start-Process -Wait run"},
               "cwd": str(owner.parent)}
    result = deny_gate.decide(payload)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "[owner-launcher]" in result["hookSpecificOutput"]["permissionDecisionReason"]
