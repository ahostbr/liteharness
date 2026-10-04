"""The deny floor: shell commands that no profile, standing rule or flag can run.

ONE RULE SET, TWO RUNTIMES. Canonical: liteharness-oss `liteharness/deny_floor.py`
(the Claude Code PreToolUse hook). LiteTUI vendors a byte-identical copy at
`src/litetui/deny_floor.py` (its tool-policy deny gate), because neither runtime
can import the other: LiteTUI's venv has no liteharness and the hook's Python
has no litetui. Edit the canonical file, then run LiteTUI's
`scripts/sync_deny_floor.py`; a LiteTUI test fails while the two differ.

Why it exists (2026-09-26 16:45): an AUTONOMOUS seat ran
`$home='...'; Remove-Item $home -Recurse -Force -ErrorAction SilentlyContinue`.
$HOME is read-only in PowerShell, the assignment failed without stopping the
script, and the user's profile folder was deleted. The command WAS classified
as destructive; the profile allowed it anyway. This floor sits below every
profile.

Four rules, all about what the command TARGETS, never about how it is spelled:

  home-variable-delete    any delete whose target is a home-directory variable
                          ($home, $HOME, ~, $env:USERPROFILE, %USERPROFILE%,
                          $env:HOME, ...). A string classifier cannot know what
                          such a variable holds at run time, so the literal is
                          refused whatever the script claims to assign it.
  protected-root-delete   a RECURSIVE delete whose resolved target is, or
                          contains, a drive root, a user profile root, the
                          profile's .claude/.codex/.liteharness/.litesuite, a
                          git repository root (a `.git` DIRECTORY; a worktree's
                          `.git` file is not one), or a folder containing the
                          workspace (the workspace itself only when it is a repo).
  owner-launcher          every parser-recognized executable `run` / `run.bat`
                          launcher is refused, without private path identity.
                          This includes bare, relative and absolute spellings,
                          nonexistent files and another checkout's launcher;
                          resolution, PATH, PATHEXT and environment/configuration
                          claims do not narrow the refusal. Existing reader and
                          proven literal-data exclusions remain: mentioning a
                          launcher is not necessarily executing it. This is a
                          bounded command recognizer, not a sandbox for arbitrary
                          scripts, variables, aliases or custom launcher names.
                          Agents use `liteharness spawn` for managed seats.
  jobs-file               writing a LiteTUI data root's `jobs.json`: the file
                          whose rows LiteTUI fires at their RECORDED level, so
                          since T1082 a written row is authority (T1085; Dijkstra
                          f0ae21c1 P1). The target is a file named jobs.json whose
                          folder holds `src/litetui` (a checkout: the default data
                          root), or `.litetui-data.json` (the marker every data root
                          gets on launch, check_data_version; the user's LiteGUI root
                          is not a checkout), or is $LITETUI_DATA_ROOT. A redirect,
                          tee / Out-File / Set-Content / Add-Content / New-Item, a
                          copy ONTO it, any move or rename of it, a delete, an
                          editor, sed -i or `python -c` are writes; a pure reader
                          (cat, type, Get-Content, rg, a copy FROM it, ...)
                          is not. `write_refusal` judges a Write/Edit tool's path
                          by this rule ALONE. LiteTUI passes `jobs=False`: there the
                          rule is seat-dependent (its seat_authority), because the
                          user's own seat may write its schedule. Literal Git reads of the protected schedule are allowed;
                          redirects and native output options defeat that exemption. Relocated Git writers
                          with uncertain targets are refused conservatively. Windows
                          aliases of the name (`jobs.json::$DATA`, `:x`, a trailing
                          dot or space) are the same file (canonical_name, A1).

Relative targets resolve against the folder the command is IN at that point:
`cd ~; Remove-Item * -Recurse` is judged as a delete of the profile, because a
cd / chdir / pushd / Set-Location / sl / Push-Location earlier in the same
command moves the base the later targets resolve against.

Ceiling (deliberate): a target, or a cd, held in an arbitrary variable or
expression (`$x`, `$tmp`, `([Environment]::GetFolderPath('UserProfile'))`) is
not resolvable here and is not refused; a delete run from inside a script file
is out of reach; a `find` narrowed by an -o chain or a regex alternation is
taken at its word; a command substitution whose OUTPUT runs as a command
(`$(ls ~) | xargs rm -rf`) is not followed. This is a floor under the danger table, not a sandbox.
The same ceiling holds for the launcher: a path built in a variable, a
`Start-Process -WorkingDirectory` that moves the base, a script that calls
run.bat itself, a renamed copy in the same folder (run.bat opens with
`cd /d "%~dp0"`), a reader's own exec feature (a git `!` alias,
-c core.pager/core.editor, rebase -x; sed's `e`; vim/less `!`) that runs a
path the reader rule excuses, or a spelling that does not resolve here (an admin share
`\\\\host\\C$\\...`, an 8.3 short name) is not seen. Command-position recognition
is intentionally limited to direct launches and the shell wrappers below;
arguments and quoted prose are not treated as executable paths. D3 additionally
recognizes leading complete non-nested PowerShell block comments, simple literal
assignment/return command positions, Start-Process/saps with -Wait/-NoNewWindow/
-PassThru/-FilePath, and finite nohup/time/timeout/exec/command/env wrapper forms
(including literal environment assignments and cmd /v:on|off /e:on|off). Unknown
options, sudo, non-leading/nested block comments and computed values remain
outside this grammar; command -v/-V and option operands are not launches.
Complete literal
PowerShell here-strings in standalone output / terminal Add-Content, Set-Content
or Out-File expressions are data for the launcher rule only when the caller
proves the interpreter with shell="powershell". Unknown / other shells retain
full scanning; syntax or a command's shell argument is not proof. Expandable strings,
execution sinks and ambiguous expression contexts remain scanned; this is a
small conservative allowlist, not a shell parser or a general quotation filter.
The jobs-file ceiling is the same shape: a path in a variable, a write from
inside a script file or through a reader's own exec feature, an 8.3 short name,
an admin share or a \\\\?\\ prefix, a link made to jobs.json BEFORE this rule was
live, a data root known only to another process's environment, and writers that
are neither hooked agents nor LiteTUI seats (a Codex CLI seat, a plain
subprocess). A data root whose LiteTUI never ran carries no marker, and no
jobs.json either. `jobs.json.lock` is not protected (holding it can stall a
save, never schedule anything). Known over-blocks, fail-safe and only for a
protected jobs.json: `python -m json.tool jobs.json`, `sed -n p jobs.json`, an
editor opened on it, robocopy naming it, and any worktree or test root that
carries the marker (tests write it in-process, never through an agent's tool).
"""
from __future__ import annotations

import fnmatch
import os
import re
import unicodedata
from pathlib import Path

HOME_VARIABLES = frozenset({
    "~", "$home", "${home}", "$env:home", "${env:home}",
    "$env:userprofile", "${env:userprofile}", "$userprofile", "${userprofile}",
    "%userprofile%", "%home%", "%homedrive%%homepath%",
})
HARNESS_DIRS = (".claude", ".codex", ".liteharness", ".litesuite")
#: Launcher recognition is identity-free: no environment/config may hide a launcher.

#: A delete verb at a word boundary. `find` counts only with -delete / -exec rm.
_VERB = re.compile(
    r"(?i)(?<![\w.$-])(remove-item|rimraf|rmdir|rm|rd|del|erase|ri|find)"
    r"(?:\.exe)?(?![\w.:\\-])")
#: A shell word: quoted runs may hold spaces, `"$HOME"/*` stays one word, and an
#: unbalanced quote (the end of a `-c "..."` string) is a word of its own.
_TOKEN = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s"'])+|["']""")
_CMD_VERBS = frozenset({"rd", "rmdir", "del", "erase"})  # take cmd.exe /s /q flags
#: PowerShell Remove-Item parameters whose value is a PATTERN that narrows what
#: is deleted, never a target (review 10cfe750). Prefixes of 3+ letters count.
_PATTERN_PARAMS = ("include", "exclude", "filter")
#: A find predicate that narrows what is deleted. Without one, `find X -delete`
#: (even with `-type f`) empties X, so it is judged as a recursive delete of X.
_FIND_FILTERS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex",
    "-iregex", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin",
    "-size", "-empty", "-user", "-group", "-perm", "-links", "-inum", "-samefile",
})
#: ...except a name/path/regex test whose pattern is only wildcards: `-name '*'`
#: matches everything, so it narrows nothing (review a93de8a9 R2).
_PATTERN_TESTS = frozenset({"-name", "-iname", "-path", "-ipath", "-wholename",
                            "-iwholename", "-regex", "-iregex"})


#: Folders a name pattern must not be aimed at (review 1daaa224 S2).
_PROTECTED_NAMES = (*HARNESS_DIRS, ".git")


def _find_filtered(words: list[str]) -> bool:
    """Does this find expression (lower-case, unquoted words) narrow its matches?

    Not narrowing: a negated test (`! -name x` keeps everything else, S1); a
    pattern of wildcards only (R2); a name or path pattern whose LAST segment
    matches a protected folder's name (`-name .claude`, `-name '.c*'` S2;
    `-path '*/.claude'` F-B)."""
    for i, word in enumerate(words):
        if i and words[i - 1] in ("!", "-not"):
            continue
        if word in _PATTERN_TESTS:
            pattern = words[i + 1] if i + 1 < len(words) else ""
            if re.fullmatch(r"[*?.+]*", pattern):
                continue
            last = re.split(r"[\\/]", pattern)[-1]
            if word not in ("-regex", "-iregex") and any(
                    fnmatch.fnmatchcase(name, last) for name in _PROTECTED_NAMES):
                continue
            return True
        if word in _FIND_FILTERS:
            return True
    return False



#: A change of directory; the words after it name the new base.
_CD = re.compile(
    r"(?i)(?<![\w.$-])(?:cd|chdir|pushd|set-location|sl|push-location)(?![\w.:\\-])")
#: `git` STARTING a command, right before the verb, on the same line. `\s+`
#: here crossed a newline, so a line ending in "git" excused the delete on the
#: next line (review 2be2a62c N1: "ls .git\nrm -rf ~" was allowed).
_GIT_VERB = re.compile(r"(?i)(?:^|[;&|\n(])[ \t]*git[ \t]+\Z")  # \Z: `$` matches before a final \n
_GLOB = re.compile(r"[*?]")
_MSYS = re.compile(r"^/([a-z])(/.*)?$", re.IGNORECASE)

#: The last segment of LiteTUI's launcher as a shell word ends it.
_LAUNCH = re.compile(r"(?i)run(?:\.bat)?(?=$|[\s\"'`;&|)}])")
#: The path in front of it: the longest run of path characters.
_PATH_TAIL = re.compile(r"[\w.~$%:\\/-]*\Z")
#: Words that pass a command on rather than being it (`cmd /d /c type x`).
_WRAPPERS = frozenset({
    "cmd", "cmd.exe", "/c", "/d", "/k", "/s", "/q", "call", "start",
    "powershell", "powershell.exe", "pwsh", "pwsh.exe", "-c", "-command",
    "-nop", "-noprofile", "-noninteractive", "bash", "sh",
})
#: Commands that read or inspect a file without running it.
_READERS = frozenset({
    "cat", "type", "gc", "get-content", "more", "less", "head", "tail", "bat",
    "grep", "egrep", "rg", "findstr", "select-string", "sls", "wc", "git",
    "diff", "fc", "code", "notepad", "vim", "vi", "nano", "sed", "awk", "ls",
    "dir", "get-item", "gi", "get-childitem", "gci", "test-path", "stat", "file",
    "xxd", "od", "sha256sum", "get-filehash",
    # Copies and moves carry the file; they never run it.
    "copy", "cp", "copy-item", "xcopy", "robocopy", "move", "mv", "move-item",
    # Output heads print their argument and never run it: agents echo file
    # names in status lines all the time.
    "echo", "write-output", "write-host", "printf",
})

#: The schedule file as a shell word ends it (T1085).
#: ...with any Windows alias of it (Dijkstra 69c7c209 A1): a stream suffix
#: (`::$DATA`, `:x`) and trailing dots name the same file. A trailing space is
#: a separator unquoted, and inside quotes the path tail stops before it anyway.
_JOBS = re.compile(r"(?i)jobs\.json(?::[^\s\"'`;&|)<>,]*)?\.*(?=$|[\s\"'`;&|)<>,])")
#: A redirect straight onto the path in front of it: `> jobs.json`, `2>>"x\jobs.json"`.
_REDIRECT_ONTO = re.compile(r"(?<![<>=-])>{1,2}\s*[\"']?\Z")
#: Heads that only READ the schedule file. Not the T1054 reader set: an editor,
#: sed/awk (-i) and a copy or move can WRITE it. An output head passes only when
#: no redirect lands on the file, which is checked first.
_JOBS_READERS = frozenset({
    "cat", "type", "gc", "get-content", "more", "less", "head", "tail", "bat",
    "grep", "egrep", "rg", "findstr", "select-string", "sls", "wc",
    "diff", "fc", "ls", "dir", "get-item", "gi", "get-childitem", "gci", "test-path",
    "stat", "file", "xxd", "od", "sha256sum", "get-filehash",
    "echo", "write-output", "write-host", "printf",
})
#: Copies read their SOURCE: only a copy whose destination is the file writes it.
_JOBS_COPIES = frozenset({"cp", "copy", "copy-item", "cpi", "xcopy"})

#: Recognize complete PowerShell here-strings, including expandable ones so
#: literal-looking text inside an expandable body cannot gain an exemption.
_HERE_STRING = re.compile(r"@(['\"])[ \t]*\r?\n.*?^\1@", re.MULTILINE | re.DOTALL)
#: Deliberately small grammar for terminal writer arguments: no expressions,
#: substitutions, redirects, continuation backticks or further pipelines.
_DATA_WRITER = re.compile(
    r"\|[ \t]*(?:Add-Content|Set-Content|Out-File)"
    r"(?:[ \t]+(?:[\w./:\\-]+|'[^'\r\n]*'|\"[^\"$`\r\n]*\"))*[ \t]*",
    re.IGNORECASE)


def _literal_here_data(command: str) -> list[tuple[int, int]]:
    """Owner-launcher-only exemption for provably inert literal expressions.

    This is not a PowerShell parser. Fail closed outside standalone expressions
    and the three terminal writers. Prefixes containing expression/quote/shell
    syntax remain scanned, including scriptblock/interpreter arguments. Earlier
    accepted data expressions are masked only for this prefix check; deletion
    and cd matching still see the original command, as before.
    """
    # PowerShell recognizes smart single quotes and bare CR line breaks. Our
    # ASCII / LF grammar could swallow an earlier real terminator and commands
    # after it. Do not widen the grammar: any such syntax anywhere restores
    # the original full scan, including when it occurs outside a here-string.
    if any(quote in command for quote in "\u2018\u2019\u201a\u201b") or re.search(r"\r(?!\n)", command):
        return []
    spans = []
    prefix_view = command
    for match in _HERE_STRING.finditer(command):
        if match.group(1) != "'":
            continue
        prefix = prefix_view[:match.start()]
        # Restrict preceding code to simple top-level statements, never an
        # enclosing expression or a continuation from an invocation.
        if not re.fullmatch(r"[\w\s./:\\;-]*", prefix):
            continue
        if re.split(r"[;\n]", prefix)[-1].strip():
            continue
        end = match.end()
        boundary = re.search(r"[;\r\n]", command[end:])
        stop = end + boundary.start() if boundary else len(command)
        suffix = command[end:stop].strip(" \t")
        if suffix and not _DATA_WRITER.fullmatch(suffix):
            continue
        # PowerShell may continue a pipeline on the following line. A newline
        # alone therefore does not prove the string's value remains inert.
        if stop < len(command) and command[stop] != ";":
            following = command[stop:].lstrip()
            # Comments can hide a continued pipeline. Do not try to parse
            # comments (especially block comments); leave the body scanned.
            if following.startswith(("|", "#", "<#")):
                continue
        spans.append((match.start(), end))
        prefix_view = prefix_view[:match.start()] + " " * (stop - match.start()) + prefix_view[stop:]
    return spans



def canonical_name(name: str) -> str:
    """The file name Windows OPENS for `name` (its last path segment), lower-cased.

    A stream suffix (`::$DATA` is the default stream, `:x` a named one) and
    trailing dots and spaces are not part of it: measured with open() by
    Dijkstra (69c7c209 A1), `jobs.json::$DATA`, `jobs.json.` and "jobs.json "
    each overwrote jobs.json. A drive-relative `C:name` keeps its name. The ONE
    definition: is_jobs_file, the shell target and LiteTUI's seat all use it (the
    gate pre-filters on the substring, which this can never escape: the result is
    a prefix of the lowered segment)."""
    segments = re.split(r"[\\/]", name)
    last = segments[-1]
    if len(segments) == 1 and re.match(r"[A-Za-z]:", last):
        last = last[2:]
    return last.split(":", 1)[0].rstrip(" .").lower()


def dealias(path: str) -> str:
    """`path` with its last segment reduced to the file Windows opens (case kept),
    so a stream suffix never reaches path resolution (a `$DATA` reads as a variable)."""
    head, sep, last = path.replace("/", "\\").rpartition("\\")
    drive = ""
    if not sep and re.match(r"[A-Za-z]:", last):
        drive, last = last[:2], last[2:]
    return head + sep + drive + last.split(":", 1)[0].rstrip(" .")


def is_jobs_file(path) -> bool:
    """Is `path` a LiteTUI data root's jobs.json (T1085)? Its name, and a folder
    that holds src/litetui (a checkout, the default root), or the
    .litetui-data.json marker every data root gets on launch, or that IS
    $LITETUI_DATA_ROOT."""
    if path is None:
        return False
    path = Path(path)
    if canonical_name(path.name) != "jobs.json":
        return False
    folder = path.parent
    if (folder / "src" / "litetui").is_dir() or (folder / ".litetui-data.json").is_file():
        return True
    root = os.environ.get("LITETUI_DATA_ROOT")
    if not root:
        return False
    try:
        root_path = Path(root).expanduser().resolve()
    except OSError:
        return False
    return os.path.normcase(str(root_path)) == os.path.normcase(str(folder))


def _jobs_say(target: Path) -> str:
    return _say("jobs-file",
                f"it writes {target}, a LiteTUI data root's schedule file: since T1082 "
                "a job's recorded level IS its authority, so a written row runs at that "
                "level in the user's own LiteTUI",
                "Schedule with /cron or LiteTUI's schedule tools instead.")


def write_refusal(path, workspace, home=None) -> str | None:
    """The jobs-file refusal for a Write/Edit tool's `file_path`, or None. This
    rule ALONE: a tool write never meets the delete or launcher rules."""
    if not isinstance(path, str) or not path:
        return None
    target = _resolve(dealias(path), Path(workspace),
                      Path(home) if home is not None else Path.home())
    return _jobs_say(target) if is_jobs_file(target) else None


def shell_commands(command: str, shell: str | None = None, *, _depth: int = 0) -> list[dict]:
    """Bounded literal argv/control view, shared by the floor and prompt policy.

    Quotes protect separators, not executable substitutions. Bash backslashes,
    PowerShell backticks/doubled single quotes and cmd carets are distinct. An
    unknown interpreter is the conservative union of those interpretations.
    This does not resolve variables, aliases, scripts or eval into runtime effects.
    Each token retains its source span; redirections are separate from argv.
    """
    if shell not in {"bash", "powershell", "cmd"}:
        result = []
        for dialect in ("bash", "powershell", "cmd"):
            result.extend(shell_commands(command, dialect, _depth=_depth))
        return result
    if _depth >= 16:
        # Keep an opaque, incomplete span at the bound; never erase commands.
        return [{"words": [("__opaque__", 0, 0), (command, 0, len(command))], "redirects": [],
                 "raw": command, "shell": shell, "complete": False}]
    result = []
    heredoc = False
    here_inputs = []
    words = []
    redirects = []
    extras = []
    token = ""
    start = None
    quote = ""
    pending = None
    segment_start = 0

    def flush(stop):
        nonlocal token, start, pending
        if start is not None:
            word = (token, start, stop)
            if pending is not None:
                redirects.append((pending, word))
                pending = None
            else:
                words.append(word)
        token, start = "", None

    def finish(stop):
        nonlocal words, redirects, extras, segment_start, pending, here_inputs
        flush(stop)
        if words or redirects:
            result.append({"words": words, "redirects": redirects,
                           "raw": command[segment_start:stop], "shell": shell,
                           "complete": not quote and pending is None, "heredoc": heredoc,
                           "here_inputs": here_inputs, "separator": command[stop:stop + 1]})
        result.extend(extras)
        words, redirects, extras, pending, here_inputs = [], [], [], None, []
        segment_start = stop + 1

    i = 0
    while i < len(command):
        ch = command[i]
        # Single quotes are ordinary characters in cmd.exe.
        if quote == "'":
            if ch == "'":
                if shell == "powershell" and command[i:i + 2] == "''":
                    token += "'"
                    i += 2
                    continue
                quote = ""
            else:
                token += ch
            i += 1
            continue
        escape = (shell == "bash" and ch == "\\" and
                  (not quote or (i + 1 < len(command) and command[i + 1] in '$`"\\\n')))
        escape |= shell == "powershell" and ch == "`"
        escape |= shell == "cmd" and ch == "^" and not quote
        if escape and i + 1 < len(command):
            if start is None:
                start = i
            if command[i + 1] != "\n":
                token += command[i + 1]
            i += 2
            continue
        # Bash ANSI-C quotes are literal decoded strings, not variables.
        if shell == "bash" and command[i:i + 2] == "$'" and not quote:
            if start is None:
                start = i
            j = i + 2
            value = ""
            while j < len(command) and command[j] != "'":
                if command[j] == "\\" and j + 1 < len(command):
                    m = re.match(r"\\(?:x[0-9a-fA-F]{1,2}|[0-7]{1,3}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})", command[j:])
                    if m:
                        digits = m.group(0)[2:] if m.group(0)[1] in "xuU" else m.group(0)[1:]
                        try:
                            value += chr(int(digits, 16 if m.group(0)[1] in "xuU" else 8))
                        except ValueError:
                            value += m.group(0)
                        j += len(m.group(0))
                        continue
                    value += {"n": "\n", "r": "\r", "t": "\t"}.get(command[j + 1], command[j + 1])
                    j += 2
                    continue
                value += command[j]
                j += 1
            token += value
            if j == len(command):
                quote = "'"
            i = min(j + 1, len(command))
            continue
        if ch == '"' or (ch == "'" and shell != "cmd"):
            if start is None:
                start = i
            if not quote:
                quote = ch
            elif quote == ch:
                quote = ""
            else:
                token += ch
            i += 1
            continue
        # Executable substitutions, including inside double quotes. Find their
        # matching boundary with this dialect's quotes/escapes, not raw split.
        process_substitution = shell == "bash" and not quote and command[i:i + 2] in {"<(", ">("}
        substitution = (command[i:i + 2] == "$(" and shell != "cmd") or process_substitution
        backtick = ch == "`" and shell == "bash"
        if substitution or backtick:
            begin = i + (2 if substitution else 1)
            j, level, inner_quote = begin, 1, ""
            while j < len(command):
                c = command[j]
                if ((shell == "bash" and c == "\\" and inner_quote != "'")
                        or (shell == "powershell" and c == "`")):
                    j += 2
                    continue
                if inner_quote:
                    if c == inner_quote:
                        inner_quote = ""
                elif c in "\"'":
                    inner_quote = c
                elif backtick and c == "`":
                    break
                elif substitution and c == "(":
                    level += 1
                elif substitution and c == ")":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            extras.extend(shell_commands(command[begin:j], shell, _depth=_depth + 1))
            if start is None:
                start = i
            token += command[i:min(j + 1, len(command))]
            i = min(j + 1, len(command))
            continue
        if not quote and ch == "#" and shell != "cmd" and start is None:
            finish(i)
            newline = command.find("\n", i)
            if newline < 0:
                break
            i = newline + 1
            segment_start = i
            continue
        if not quote and shell == "powershell" and ch == "{" and re.fullmatch(r"\s*&\s*", command[segment_start:i]):
            # Literal invoked scriptblock. Data scriptblocks passed to an output
            # command do not gain executable meaning from their spelling.
            level, j = 1, i + 1
            inner_quote = ""
            while j < len(command):
                c = command[j]
                if c == "`":
                    j += 2
                    continue
                if inner_quote:
                    if c == inner_quote:
                        inner_quote = ""
                elif c in "\"'":
                    inner_quote = c
                elif c == "{":
                    level += 1
                elif c == "}":
                    level -= 1
                    if not level:
                        break
                j += 1
            extras.extend(shell_commands(command[i + 1:j], shell, _depth=_depth + 1))
            i = min(j + 1, len(command))
            continue
        if not quote and ch in "(){}" and (shell != "powershell" or ch in "()"):
            finish(i)
            i += 1
            segment_start = i
            continue
        if not quote and ch in ("|&\n\r" if shell == "cmd" else ";|&\n\r"):
            # PowerShell's invocation operator is not a separator.
            if ch == "&" and shell == "powershell" and not words and start is None:
                i += 1
                continue
            finish(i)
            i += 1
            while i < len(command) and command[i] == ch and ch in "|&":
                i += 1
            segment_start = i
            continue
        if not quote and shell == "bash" and command[i:i + 2] == "<<":
            # Recognize the operator in the current lexical context, never a
            # quoted outer -c payload. The actual argv here identifies its sink.
            match = re.match(r"<<(-?)[ \t]*(['\"])([A-Za-z_][A-Za-z_0-9]*)\2", command[i:])
            newline = command.find("\n", i)
            if match and newline >= 0:
                ending = re.search(r"(?m)^" + (r"\t*" if match.group(1) else "") + re.escape(match.group(3)) + r"(?:\r?$)", command[newline + 1:])
                if ending:
                    flush(i)
                    body_start = newline + 1
                    body_stop = body_start + ending.start()
                    stop = body_start + ending.end()
                    here_inputs.append(command[body_start:body_stop])
                    heredoc = True
                    command = command[:body_start] + " " * (stop - body_start) + command[stop:]
                    i += len(match.group(0))
                    continue
        if not quote and ch in "<>":
            # A numeric file descriptor adjacent to > is not an argv operand.
            if start is not None and token.isdecimal() and command[start:i] == token:
                token, start = "", None
            flush(i)
            j = i + 1
            while j < len(command) and command[j] == ch:
                j += 1
            # Descriptor duplication does not open a file. Bash >&file does.
            op = command[i:j]
            if j < len(command) and command[j] == "&":
                j += 1
                m = re.match(r"(?:[0-9]+|-)(?=$|[\s;&|])", command[j:])
                if m:
                    i = j + len(m.group(0))
                    continue
                op += "&"
            pending = op
            i = j
            continue
        if not quote and ch.isspace():
            flush(i)
        else:
            if start is None:
                start = i
            token += ch
        i += 1
    finish(len(command))
    # Input redirections can precede argv and a pipe consumer can occur AFTER
    # the << operator. Resolve literal stdin execution only after full parsing.
    def stdin_head(part):
        argv = [word[0] for word in part["words"]]
        while argv and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", argv[0]):
            argv = argv[1:]
        while argv and argv[0].lower() in {"env", "sudo", "command"}:
            argv = argv[1:]
            while argv and (argv[0].startswith("-") or "=" in argv[0]):
                takes_value = argv[0] in {"-u", "-g", "--user", "--group", "--unset"}
                argv = argv[2:] if takes_value else argv[1:]
        head = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") if argv else ""
        # A literal command/script argument takes precedence over stdin code.
        args = argv[1:]
        consumes = head in {"bash", "sh", "python", "python3", "node"}
        if any(arg in {"-c", "-e", "-m", "--command", "--eval"} for arg in args):
            consumes = False
        if any(not arg.startswith("-") for arg in args if arg != "-"):
            consumes = False
        return head, consumes

    here_commands = []
    for n, part in enumerate(result):
        for body in part.get("here_inputs", []):
            consumer = part
            head, consumes = stdin_head(consumer)
            # Only literal cat passthrough is proved here, not arbitrary pipeline
            # transformations. A following literal interpreter is the code sink.
            if head == "cat" and part.get("separator") == "|" and n + 1 < len(result):
                consumer = result[n + 1]
                head, consumes = stdin_head(consumer)
            if consumes:
                if head in {"bash", "sh"}:
                    here_commands.extend(shell_commands(body, "bash", _depth=_depth + 1))
                else:
                    here_commands.append({"words": [(head, 0, 0), ("-e" if head == "node" else "-c", 0, 0), (body, 0, 0)],
                                          "redirects": [], "raw": body, "shell": shell, "complete": True})
    result.extend(here_commands)
    # Only actual shell heads may give -c/-Command//c executable meaning.
    expanded = []
    for part in result:
        argv = [word[0] for word in part["words"]]
        if not argv:
            expanded.append(part)
            continue
        head = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        dialect = {"bash": "bash", "sh": "bash", "pwsh": "powershell",
                   "powershell": "powershell", "cmd": "cmd"}.get(head)
        option = None
        if dialect:
            n = 1
            while n < len(argv):
                word = argv[n].lower()
                wanted = {"bash": {"-c"}, "powershell": {"-c", "-command"}, "cmd": {"/c", "/k"}}[dialect]
                if word in wanted:
                    option = n
                    break
                if word == "--" or not word.startswith("/" if dialect == "cmd" else "-"):
                    break
                if dialect == "powershell" and word in {"-file", "-f", "-encodedcommand", "-enc"}:
                    break
                if dialect == "powershell" and word in {"-executionpolicy", "-ep", "-workingdirectory"}:
                    n += 1
                n += 1
        if dialect and option is not None and option + 1 < len(argv):
            payload = argv[option + 1] if dialect == "bash" else " ".join(argv[option + 1:])
            expanded.extend(shell_commands(payload, dialect, _depth=_depth + 1))
            # Outer redirection still writes in the caller's cwd.
            if part["redirects"]:
                expanded.append({**part, "words": []})
        else:
            expanded.append(part)
    return expanded


def _git_read_words(args: list[str], redirects=()) -> bool:
    """Finite literal Git readers; an output option/redirect defeats the waiver."""
    if redirects or any(re.search(r"[$`()<>]", word) for word in args):
        return False
    i = 0
    while i < len(args):
        if args[i] == "--no-pager":
            i += 1
        elif args[i] in {"-C", "--git-dir", "--work-tree"}:
            if i + 1 >= len(args) or not args[i + 1] or args[i + 1].startswith("-"):
                return False
            i += 2
        elif (args[i].startswith("-C") and len(args[i]) > 2
              or any(args[i].startswith(option + "=") and args[i] != option + "="
                     for option in ("--git-dir", "--work-tree"))):
            i += 1
        else:
            break
    if i == len(args):
        return False
    verb, operands = args[i], args[i + 1:]
    # These options can execute configured helpers rather than just read Git
    # objects. Global config/pager/exec-path switches never pass the finite
    # prefix grammar above; reject their operand-position spellings as well.
    if any(word.startswith(("--out", "-o")) or word.partition("=")[0] in {
        "--ext-diff", "--textconv", "--config", "--config-env", "--exec-path",
        "--paginate", "--pager", "--difftool", "--gui", "--extcmd"
    } for word in operands):
        return False
    if verb == "worktree":
        return (operands[:1] == ["list"]
                and all(word in {"--porcelain", "-z", "-v", "--verbose"}
                        for word in operands[1:]))
    if verb == "branch":
        # --list is required. Other branch operations write refs/config even
        # when mixed with it; do not infer a read from one option in argv.
        return ("--list" in operands and all(
            not word.startswith("-") or word in {
                "--list", "-l", "--all", "-a", "--remotes", "-r", "--verbose", "-v", "-vv"
            } for word in operands))
    return verb in {
        "show", "diff", "status", "log", "blame", "ls-files", "ls-tree", "rev-parse", "check-ignore"
    }


def jobs_git_refusal(command, workspace, home=None, *, shell=None) -> str | None:
    """Protected schedule Git writes; actual command heads alone get read waivers.

    Relocated writes retain the conservative guard. This is literal syntax
    classification, not proof about aliases, variables or a script's effects.
    """
    if not isinstance(command, str):
        command = " ".join(map(str, command or ()))
    home = Path(home) if home is not None else Path.home()
    base = Path(workspace)
    parts = shell_commands(command, shell)
    env_assignment = any(re.match(
        r'(?i)^(?:(?:set|export)\s+)?(?:\$env:)?(?:GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)\s*=',
        " ".join(w[0] for w in part["words"])) for part in parts)
    inherited_relocation = False
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        value = os.environ.get(name)
        target = _resolve(value, base, home) if value else None
        if target is not None and any(is_jobs_file(folder / "jobs.json")
                                      for folder in (target, *target.parents)):
            inherited_relocation = True
            break
    for part in parts:
        words = [w[0] for w in part["words"]]
        while words and re.match(r"(?i)^(?:GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE)=", words[0]):
            words = words[1:]
        if not words or words[0].replace("\\", "/").rsplit("/", 1)[-1].lower() not in {"git", "git.exe"}:
            continue
        args = words[1:]
        if part["complete"] and _git_read_words(args, part["redirects"]):
            continue
        relocating = (env_assignment or inherited_relocation
                      or any(word.startswith(("-C", "--git-dir", "--work-tree"))
                             for word in args)
                      or any(re.search(r"(?i)core\.worktree|GIT_DIR|GIT_WORK_TREE|GIT_INDEX_FILE", word)
                             for word in args))
        # Shell redirects are NOT Git operands. Non-Git reason routing stays
        # with jobs_write_target/T1085, even when the payload happens to say git.
        targets = args + [word[0] for op, word in part["redirects"] if op.startswith(">")]
        for word in targets:
            path = word.partition("=")[2] if "=" in word else word
            if not _JOBS.search(path):
                continue
            if relocating:
                return _say("jobs-file", "a relocated Git write names jobs.json; "
                            "its schedule target cannot be proven outside a data root",
                            "Read schedules with a file-read tool instead.")
            if ":" in path and not re.match(r"^[A-Za-z]:", path):
                path = path.partition(":")[2]
            if reason := write_refusal(path, base, home):
                return reason
        if relocating:
            return _say("jobs-file", "a repo-relocating Git write may change a LiteTUI "
                        "schedule outside the shell cwd; its target is not proven safe",
                        "Use a normal working directory and literal non-schedule paths instead.")
    return None


def jobs_write_target(command, workspace, home=None, *, shell=None) -> Path | None:
    """Protected schedule write target, using real argv and control boundaries."""
    if not isinstance(command, str):
        command = " ".join(map(str, command or ()))
    home = Path(home) if home is not None else Path.home()
    # Unknown shells are independent interpretations, not three consecutive cwd changes.
    dialects = (shell,) if shell in {"bash", "powershell", "cmd"} else ("bash", "powershell", "cmd")
    for dialect in dialects:
        base = Path(workspace)
        for part in shell_commands(command, dialect):
            words = [w[0] for w in part["words"]]
            head = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if words else ""
            for op, word in part["redirects"]:
                if op.startswith(">"):
                    target = _resolve(dealias(word[0]), base, home)
                    if is_jobs_file(target):
                        return target
            if head in {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}:
                args = [word for word in words[1:] if word.lower() not in {"/d", "-path", "-literalpath"}]
                if args:
                    base = _resolve(args[0], base, home)
                continue
            if not words or head in _JOBS_READERS:
                continue
            args = words[1:]
            target_base = base
            if head in {"git", "git.exe"}:
                if part["complete"] and _git_read_words(args, part["redirects"]):
                    continue
                for n, word in enumerate(args[:-1]):
                    if word == "-C":
                        target_base = _resolve(args[n + 1], target_base, home)
                    elif word.startswith("-C") and len(word) > 2:
                        target_base = _resolve(word[2:], target_base, home)
            for n, word in enumerate(args):
                path = word
                param = ""
                if word.startswith("-") and ":" in word:
                    param, _, path = word.partition(":")
                elif word.startswith("-") and "=" in word:
                    _, _, path = word.partition("=")
                # Literal paths retain spaces. Embedded interpreter code keeps
                # the historical finite path scan (no inner-language execution).
                matches = list(_JOBS.finditer(path))
                for match in matches:
                    if match.end() == len(path) and match.start() >= 0:
                        raw = path
                    else:
                        prefix = _PATH_TAIL.search(path[:match.start()]).group(0)
                        if prefix and prefix[-1] not in "\\/":
                            continue
                        raw = prefix + "jobs.json"
                    target = _resolve(dealias(raw), target_base, home)
                    if not is_jobs_file(target):
                        continue
                    if head in _JOBS_COPIES:
                        previous = args[n - 1].lower() if n else ""
                        named_source = param.lower() in {"-path", "-literalpath"} or previous in {"-path", "-literalpath"}
                        named_dest = param.lower().startswith("-d") or previous.startswith("-d")
                        has_named_dest = any(a.lower().startswith("-destination") for a in args)
                        destination = named_dest or (not named_source and not has_named_dest and n == len(args) - 1)
                        if not destination:
                            continue
                    return target
    return None


def refusal(command, workspace, home=None, *, jobs=True, shell: str | None = None) -> str | None:
    """Refusal sentence, or None. Only a trusted runtime may prove `shell`.

    Omitted / unknown shell retains the original full scan. Never infer this
    context from command syntax or an agent-supplied argument.
    """
    if not isinstance(command, str):
        command = " ".join(map(str, command or ()))
    workspace = Path(workspace)
    home = Path(home) if home is not None else Path.home()
    if jobs and (reason := jobs_git_refusal(command, workspace, home, shell=shell)):
        return reason
    if jobs and (target := jobs_write_target(command, workspace, home, shell=shell)):
        return _jobs_say(target)
    base: Path | None = workspace   # where relative targets resolve; None = unknown
    literal_data = _literal_here_data(command) if shell == "powershell" else []
    launcher_base: Path | None = workspace  # data cd text must not move a real launch
    # D1's fail-closed branch is confined to a whole interpreter/code shape.
    # Broader expressions still use the ordinary bounded command-position scan.
    code = _INTERPRETER_CODE.fullmatch(command)
    code_launches = _CODE_LAUNCH.finditer(command, code.start('token'), code.end('token')) if code else []
    steps = sorted([*((m.start(), m) for m in _CD.finditer(command)),
                    *((m.start(), m) for m in _VERB.finditer(command)),
                    *((m.start(), m) for m in _LAUNCH.finditer(command)),
                    *((m.start(), m) for m in code_launches)],
                   key=lambda step: step[0])
    for _, match in steps:
        in_data = any(start <= match.start() < end for start, end in literal_data)
        if match.re is _CD:
            base = _cd_target(command[match.end():], base, home)
            if not in_data:
                launcher_base = _cd_target(command[match.end():], launcher_base, home)
            continue
        if match.re in (_LAUNCH, _CODE_LAUNCH):
            if in_data:
                continue
            if _owner_launch(command, match, launcher_base, home, shell=shell):
                return _say("owner-launcher",
                            "it executes an owner-capable run/run.bat launcher; private "
                            "launcher identity is unavailable, so every recognized "
                            "executable spelling is denied, including other absolute paths",
                            "Launch a seat with `liteharness spawn` instead.")
            continue
        if _GIT_VERB.search(command[:match.start()]):
            continue  # `git rm` works on the index and tracked files, not a tree
        verb = match.group(1).lower()
        recursive, targets, deletes = _arguments(verb, command[match.end():])
        if not deletes:
            continue
        if verb != "find":
            targets += _pipeline_source(command[:match.start()])
        for raw in targets:
            if _normal(raw) in HOME_VARIABLES:
                return _say("home-variable-delete",
                            f"it deletes {raw!r}, a home-directory variable whose value "
                            "the host cannot see")
            if not recursive:
                continue
            resolved = _resolve(raw, base, home)
            why = resolved and _protected(resolved, workspace, home)
            if why:
                return _say("protected-root-delete",
                            f"it recursively deletes {resolved}, {why}")
    return None


def _say(rule: str, what: str,
         instead: str = "Delete a specific folder by its literal path instead.") -> str:
    return (f"DENY FLOOR [{rule}]: {what}. No tool profile, standing rule or "
            f"rewording can run this; do not retry it in another form. {instead}")


#: Anchored argv shape, not a quote scanner: exactly one quoted code token.
_INTERPRETER_CODE = re.compile(
    r'[ \t]*(?P<head>python(?:\.exe)?[ \t]+-c|node(?:\.exe)?[ \t]+-e)'
    r'[ \t]+(?P<token>"[^"\r\n]*"|\'[^\'\r\n]*\')[ \t]*', re.IGNORECASE)
#: Only this anchored code context also recognizes indexing/call adjacency.
_CODE_LAUNCH = re.compile(r'(?i)(?<![\w])run(?:\.bat)?(?![\w])')


def _launcher_quoted_argument(command: str, start: int) -> bool:
    """Only an anchored, unambiguous whole command can be quoted data.

    No partial spans, compound commands or executing reader options. Single
    quotes are not quoting in cmd, so their bodies exclude every operator and
    expansion even when ASCII double quotes toggle cmd's quote state. Unknown
    syntax restores matching; this is not a parser or general quote filter.
    """
    forbidden = "^`$\\!%\r\n" + "\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f" + "\u00ab\u00bb\u2039\u203a\uff02\uff07"
    if any(ch in forbidden or (ch != "\t" and unicodedata.category(ch) in ("Pi", "Pf", "Cc", "Cf"))
           for ch in command):
        return False
    code = _INTERPRETER_CODE.fullmatch(command)
    shape = code or re.fullmatch(
        r'[ \t]*(?:git[ \t]+(?:commit[ \t]+(?:(?:-a|--amend)[ \t]+)*(?:-m|-am|--message)|'
        r'tag[ \t]+(?:-m|--message))|echo|printf|Write-Output|Write-Host)'
        r'[ \t]+(?P<token>"[^"\'\t]*"|\'[^\'\t]*\')[ \t]*',
        command, re.IGNORECASE)
    if shape is None:
        return False
    token = shape.group('token')
    body = token[1:-1]
    if token[0] == '"' and "'" in body:
        return False
    if token[0] == "'" and any(ch in body for ch in '&|<>'):
        return False
    if code:
        # D1: this is the one place CODE is let through: exactly one print /
        # console.log call, no nested call, indexing, assignment, concatenation
        # or statement separator can turn a bare identifier into execution.
        # Anything richer mentioning the launcher is refused, not excused as
        # data. Function names are case-sensitive; executable heads are not.
        if any(ch in body for ch in ';=+[]'):
            return False
        function = r'print' if code.group('head').lower().startswith('python') else r'console\.log'
        if not re.fullmatch(function + r'[ \t]*\([^()]*\)', body):
            return False
    return shape.start('token') < start < shape.end('token') - 1


#: F1: bounded block positions, including grouping parens nested after { or ?.
#: Call-paren objects, ${variables}, quoted braces, paths and general word+brace
#: spellings remain data. Prefix-consuming arms always end AT the boundary.
_OWNER_BOUNDARY = re.compile(
    r"[;&|\r\n()`]|^\{|(?<=[\s&.|%;)?{])\{|(?<=^\()\{|"
    r"(?<=[\s&.|%;({?]\()\{|"
    r"(?<![\w.$/\\-])(?:try|do|else|finally|catch|begin|process|end|trap)\{|"
    r"(?<![\w.$/\\-])(?:function|filter)[ \t]+[A-Za-z_][\w-]*\{",
    re.IGNORECASE)
#: Narrow switch-label shapes directly inside an ALREADY accepted brace: digits,
#: quoted strings (quote doubling), default, or a brace-free scriptblock label,
#: with leading whitespace/CRLF. No scanner: sibling clauses, escaped quotes,
#: variable/expression/here-string labels and comments remain outside grammar.
#: These labels are the only accepted word/quote/} brace predecessors.
_SWITCH_LABEL_BOUNDARY = re.compile(
    r"\{\s*(?:\d+|'(?:[^'{}\r\n]|'')*'|\"(?:[^\"{}\r\n]|\"\")*\"|default|\{[^{}\r\n]*\})\{",
    re.IGNORECASE)
_EXECUTION_SINKS = frozenset({
    "start-process", "saps", "start", "invoke-item", "ii", "invoke-expression", "iex",
    "invoke-command", "icm", "start-job", "sajb", "start-threadjob", "call", "cmd",
    "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "sh",
})


def _owner_command_position(words: list[str]) -> list[str]:
    """Peel only literal, finite launcher wrappers; unknown operands stay put.

    In particular command -v/-V inspect names, env -u consumes a name, and
    timeout consumes a duration. None may turn an option operand into argv0.
    """
    while words:
        head = words[0]
        if (head in _WRAPPERS or head in {"nohup", "exec"}
                or re.fullmatch(r"[a-z_][\w]*=[^$`]*", head)):
            words = words[1:]
        elif head == "command":
            if words[1:2] in (["-v"], ["-V"]):
                return words
            words = words[2:] if words[1:2] == ["--"] else words[1:]
        elif head == "time":
            words = words[2:] if words[1:2] == ["-p"] else words[1:]
        elif head == "env":
            words = words[1:]
            while words and (words[0] in {"-i", "--ignore-environment", "--"}
                             or re.fullmatch(r"[a-z_][\w]*=[^$`]*", words[0])):
                words = words[1:]
        elif head == "timeout":
            rest = words[1:]
            if rest and re.fullmatch(r"--signal=[a-z0-9]+", rest[0]):
                rest = rest[1:]
            if not rest or not re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", rest[0]):
                return words
            words = rest[1:]
        else:
            return words
        if head in {"cmd", "cmd.exe"}:
            while words and re.fullmatch(r"/[ve]:(?:on|off)", words[0]):
                words = words[1:]
    return words


def _owner_launch(command: str, match: re.Match, base: Path | None, home: Path,
                  *, shell: str | None = None) -> bool:
    """Recognize executable run/run.bat without caller-controlled identity.

    Deliberately conservative: another absolute run.bat is denied too. Existing
    parser/data guards remain; no existence, PATH or configuration can grant
    an exception. This bounded command recognizer is not a shell sandbox.
    """
    start = match.start()
    if _launcher_quoted_argument(command, start):
        return False
    # Leading non-nested comments can precede argv0. Only a trusted PowerShell
    # context may excuse a match IN the comment; unknown shells keep scanning.
    comments = re.match(r"[ \t]*(?:<#(?:(?!<#|#>).)*#>[ \t]*)+", command, re.DOTALL)
    if comments:
        if start < comments.end() and shell == "powershell":
            return False
        if start >= comments.end():
            command = " " * comments.end() + command[comments.end():]
    prefix = _PATH_TAIL.search(command[:start]).group(0)
    prefix_length = len(prefix)
    if prefix.startswith("-"):   # -FilePath:.\run.bat names the path after the colon
        if ":" not in prefix:
            return False
        prefix = prefix.partition(":")[2]
    if prefix and prefix[-1] not in "\\/":
        return False   # `rerun`, `myrun.bat`: another name
    if match.re is _CODE_LAUNCH:
        # No new general brace boundary: only anchored richer interpreter CODE
        # mentioning the launcher takes this D1 branch, without identity narrowing.
        return True
    # The head is judged PER SEGMENT: `echo x & run.bat` runs run.bat. A `(`
    # or backtick opens a substitution that EXECUTES (`echo $(run.bat)`,
    # echo `run.bat`), so it starts a segment too. A `)` ends one: in
    # `if 1==2 (echo a) else run.bat` the command is `else`'s, not echo's (P1).
    boundaries = []
    openings = [*((m.end() - 1, None) for m in _OWNER_BOUNDARY.finditer(command[:start])),
                *((m.end() - 1, m.start()) for m in _SWITCH_LABEL_BOUNDARY.finditer(command[:start]))]
    for boundary, enclosing in sorted(openings, key=lambda opening: opening[0]):
        # Label candidates are syntax-only shapes, not authoritative shell
        # parsing. Require their enclosing brace to have been accepted already;
        # foo{10{run}, a'1'{run} and ${1}{run} must not gain a boundary.
        if enclosing is not None and enclosing not in boundaries:
            continue
        boundaries.append(boundary)
    boundary = boundaries[-1] if boundaries else -1
    segment = command[boundary + 1:start]
    # Judge the whole source token, not the truncated prefix before `run`.
    # X=run is an assignment value; X=run run has a distinct argv0 token.
    # Unlike $x=run in PowerShell, this unprefixed NAME=word never executes
    # its RHS as a command in any supported shell. Substitutions retain their
    # own boundary and are still scanned separately.
    tokens = list(re.finditer(r"(?:[^\s\"'`;&|]+|\"[^\"]*\"|'[^']*')+",
                              command[boundary + 1:]))
    token_index = next((i for i, token in enumerate(tokens)
                        if token.start() <= start - boundary - 1 < token.end()), None)
    if token_index is not None and re.match(r"[A-Za-z_][\w]*=", tokens[token_index].group()):
        preceding = [_unquote(token.group()).lower() for token in tokens[:token_index]]
        if not _owner_command_position(preceding):
            return False
    # A clearly quoted function / array argument is data, not argv0. Bare
    # words after an identifier+( remain ambiguous and are scanned fail-closed.
    function_head = re.search(r"[\w.-]+\Z", command[:boundary]) if boundary > 0 else None
    execution_sink = function_head is not None and function_head.group().lower() in _EXECUTION_SINKS
    if not execution_sink and boundary >= 0 and command[boundary] == "(" and boundary > 0 and (
            command[boundary - 1].isalnum() or command[boundary - 1] in "_@.") and (
            re.fullmatch(r"[ \t]*[\"']", command[boundary + 1:start])) and (
            command[start:].startswith(match.group() + command[start - 1])):
        return False
    before = segment[:-prefix_length] if prefix_length else segment
    expression = re.match(r"[ \t]*(?:\$[A-Za-z_][\w]*[ \t]*=[ \t]*|return[ \t]+)", before, re.IGNORECASE)
    if expression:
        before = before[expression.end():]
        if before.lstrip().startswith(("'", '"')):
            return False   # assignment/return of a string is not a command
    # Literal PowerShell argument arrays launching cmd: only /c (optionally
    # preceded by /d) places the following launcher at cmd's executable head.
    # Do not peel echo/type, arbitrary switches, variables, or string operands.
    cmd_wrapper = re.fullmatch(
        r"[ \t]*(?:start-process|saps)[ \t]+(?:-filepath[ \t]+)?"
        r"(?:cmd(?:\.exe)?|'cmd(?:\.exe)?'|\"cmd(?:\.exe)?\")[ \t]+"
        r"-argumentlist[ \t]+(?:(?:/d|'/d'|\"/d\")[ \t]*,[ \t]*)?"
        r"(?:/c|'/c'|\"/c\")[ \t]*,[ \t]*[\"']?", before, re.IGNORECASE)
    if cmd_wrapper:
        before = ""
    before = before.strip(" \t\"'")
    before = re.sub(r"\d?[<>]{1,2}&?[^\s<>&|]+", "", before)
    position = re.findall(r"[^\s\"'`]+", before.lower())
    if position[-1:] == ["-filepath:"]:
        position[-1] = "-filepath"
    position = [w.lstrip("@^") for w in position if w.lstrip("@^")]
    position = _owner_command_position(position)
    if position[:1] and position[0] in _READERS:
        return False
    # Narrow command positions: direct invocations and cmd's bounded if
    # conditions. Do not turn a later subcommand/argument into argv0.
    if position and position[0] in ("&", "do", "else"):
        position.pop(0)
    if position[:1] == ["if"]:
        condition = position[1:]
        if condition[:1] == ["not"]:
            condition = condition[1:]
        if ((len(condition) == 2 and condition[0] in ("exist", "defined", "errorlevel"))
                or (len(condition) == 1 and "==" in condition[0])):
            position = []
    if position and not (
            (position[0] in ("start-process", "saps")
             and all(w in {"-wait", "-nonewwindow", "-passthru", "-filepath"}
                     for w in position[1:])
             and ("-filepath" not in position[1:] or position[-1] == "-filepath"))
            or (position[0] in ("invoke-item", "ii") and not position[1:])):
        return False
    return True


def _unquote(word: str) -> str:
    return word.replace('"', "").replace("'", "").strip()


def _outside_quotes(word: str, chars: str) -> list[int]:
    """Indexes of `chars` in `word` that are not inside quotes."""
    quote, hits = "", []
    for i, ch in enumerate(word):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in chars:
            hits.append(i)
    return hits


def _separator(word: str) -> int:
    """Index of the first `;`, `|` or `&` outside quotes, or -1."""
    hits = _outside_quotes(word, ";|&")
    return hits[0] if hits else -1


def _pieces(word: str) -> list[str]:
    """A PowerShell comma array is ONE shell word: `'C:\\tmp\\x','C:\\data\\b'`
    (review 400014dd F-A). Split it on commas outside quotes, so every path in
    it is judged; a word with no comma comes back whole.

    Unquoted braces go first (review 10cfe750 F-C): bash brace expansion runs
    before tilde and parameter expansion, so `rm -rf {x,~}` deletes ~ and
    `~{,}` is "~ ~". Dropping the braces over-approximates concatenation
    (`/data/{a,b}` is judged as "/data/a" and "b"), which only ever
    refuses more. `${HOME}` becomes `$HOME`, still a home variable."""
    braces = set(_outside_quotes(word, "{}"))
    word = "".join(ch for i, ch in enumerate(word) if i not in braces)
    pieces, start = [], 0
    for i in _outside_quotes(word, ","):
        pieces.append(word[start:i])
        start = i + 1
    return pieces + [word[start:]]


def _arguments(verb: str, rest: str):
    """(recursive, targets, deletes) for the words after one delete verb."""
    recursive = verb == "rimraf"
    deletes = verb != "find"
    find_words, in_paths = [], True
    pattern_next = False   # the word after -Include/-Exclude/-Filter
    targets: list[str] = []
    for token in _TOKEN.findall(rest.split("\n", 1)[0]):
        if token.startswith("\\;"):
            break  # the end of a find -exec
        cut = _separator(token)
        stop = cut >= 0
        if stop:
            token = token[:cut]
        if token[:1] in (")", "}"):
            break
        token = token.rstrip(")")
        if token.count("}") > token.count("{"):
            token = token.rstrip("}")
        if pattern_next:
            pattern_next = False   # a pattern that only narrows, never a target
            if stop:
                break
            continue
        for piece in _pieces(token):
            low = _unquote(piece).lower()
            if not low or low in ("--", "{}"):
                pass
            elif verb == "find":
                in_paths = in_paths and not (low.startswith("-") or low in ("(", "!"))
                if in_paths:
                    targets.append(piece)
                else:
                    find_words.append(low)
                    if low in ("-delete", "rm", "rmdir", "remove-item", "rimraf"):
                        deletes = True
            elif low.startswith("-"):
                name, _, value = low.partition(":")
                if (name == "--recursive" or ("recurse".startswith(name[1:]) and len(name) > 1)
                        or (re.fullmatch(r"-[a-z]*r[a-z]*", name) and len(name) <= 4)):
                    recursive = True
                if _pattern_param(name):
                    pattern_next = not value   # -Include *.log: the NEXT word is a pattern
                elif value and value not in ("$true", "$false"):
                    targets.append(piece.partition(":")[2])
            elif verb in _CMD_VERBS and re.fullmatch(r"/[a-z]", low):
                recursive = recursive or low == "/s"
            else:
                targets.append(piece)
        if stop:
            break
    if verb == "find":
        recursive = not _find_filtered(find_words)
    return recursive, targets, deletes


def _pipeline_source(before: str) -> list[str]:
    """`gci $home | Remove-Item -Recurse`: the pipe's head names the target."""
    tail = before.rstrip()
    xargs = re.search(r"\|\s*xargs(?:\s+-\S+)*$", tail)
    if xargs:
        tail = tail[:xargs.start()]
    elif tail.endswith("|"):
        tail = tail[:-1]
    else:
        return []
    # Inside an enclosing script block (`& { gci ~ | ri -r }`) the head starts
    # after its UNMATCHED opener. A matched pair is part of the head: cutting at
    # every `{` turned `ls {x,~} | xargs rm -rf` into a head with no paths.
    opened = []
    for i in _outside_quotes(tail, "{()}"):
        if tail[i] in "{(":
            opened.append(i)
        elif opened:
            opened.pop()
    if opened:
        tail = tail[opened[-1] + 1:]
    head = re.split(r"[;\n]|&&|\|\|", tail)[-1].split("|")[0]
    # Grouped by shell word: every piece of one comma array is one argument.
    # A parenthesised head keeps its brackets on its words: `(gci ~) | ri -r`
    # read "~)" (review 0f435720 X3), so they are stripped as _arguments does.
    groups = [[_unquote(p).strip("()").rstrip("}") for p in _pieces(w)]
              for w in _TOKEN.findall(head)]
    words = [p for group in groups for p in group]
    if words[:1] == ["find"] and _find_filtered([w.lower() for w in words]):
        # A filtered listing names only what matched, not its root -- except a
        # home-variable root, refused whatever narrows it (review a93de8a9 R1:
        # `find $HOME -type d -name .claude | xargs rm -rf` deletes ~/.claude).
        return [w for w in words[1:] if not w.startswith("-") and _normal(w) in HOME_VARIABLES]
    paths, pending, narrowed = [], "", False
    for group in groups[1:]:
        flag = group[0].lower().partition(":")[0]
        if pending:   # the pattern(s) after -Include/-Exclude/-Filter
            narrowed = narrowed or _pattern_narrows(pending, group)
            pending = ""
        elif flag.startswith("-"):
            param = _pattern_param(flag)
            if param and ":" in group[0]:   # -Filter:a,b
                value = [group[0].partition(":")[2], *group[1:]]
                narrowed = narrowed or _pattern_narrows(param, value)
            else:
                pending = param
        else:
            paths += [w for w in group if w and not w.startswith("-")]
    if narrowed and not paths:
        return []   # `gci -Filter *.log | ri`: only what matched, not the folder
    # A listing with no path lists the current folder (review 2be2a62c N2:
    # `cd ~; ls | xargs rm -rf`); "." resolves against the tracked base. Empty
    # words (a lone quote) are dropped first, so "." still applies to them.
    return paths or ["."]


def _pattern_param(flag: str) -> str:
    """"include"/"exclude"/"filter" for that parameter (3+ letter prefix), else ""."""
    return next((p for p in _PATTERN_PARAMS if len(flag) >= 4 and p.startswith(flag[1:])), "")


def _pattern_narrows(param: str, pieces: list[str]) -> bool:
    """Does this -Include/-Exclude/-Filter value narrow what a listing returns?

    Never for -Exclude: it is a NEGATION and lists everything else, folders
    included (review 0f435720 X2). Otherwise only when EVERY piece is a real
    pattern: wildcards-only narrows nothing, and a piece that matches a
    protected folder's name aims AT it (X1, the S2 test)."""
    if param == "exclude":
        return False
    for piece in pieces:
        low = piece.lower()
        if re.fullmatch(r"[*?.]*", low) or any(
                fnmatch.fnmatchcase(name, low) for name in _PROTECTED_NAMES):
            return False
    return True


def _normal(raw: str) -> str:
    """Lower-case, unquoted, with trailing separators and globs removed."""
    text = _unquote(raw).lower()
    while True:
        text = text.rstrip("/\\")
        head, sep, last = text.replace("\\", "/").rpartition("/")
        if sep and _GLOB.search(last):
            text = text[:len(head)]
            continue
        return text


def _cd_target(rest: str, base: Path | None, home: Path) -> Path | None:
    """The base after `cd <rest>`: the first word that is not a flag. A bare
    `cd` goes home (bash); `-` or a variable leaves the base unknown."""
    for token in _TOKEN.findall(rest.split("\n", 1)[0]):
        cut = _separator(token)
        word = _unquote(token[:cut] if cut >= 0 else token)
        low = word.lower()
        if low.startswith("-") and ":" in word:
            word = low = word.partition(":")[2]   # -Path:C:\x names the folder (N3)
        if word and not (low.startswith("-") or re.fullmatch(r"/[a-z]", low)):
            return _resolve(word, base, home)
        if word == "-":
            return None
        if cut >= 0:
            break
    return home.resolve()


def _resolve(raw: str, base: Path | None, home: Path) -> Path | None:
    """The folder a target names, or None when a variable (or an unknown base
    under a relative target) hides it."""
    if not _unquote(raw):
        return None  # a lone quote is no path; "" used to become "/", a drive root
    parts = re.split(r"[\\/]", _unquote(raw))
    while len(parts) > 1 and parts[-1] == "":
        parts.pop()  # trailing separators
    while parts and _GLOB.search(parts[-1]):
        parts.pop()  # `dir/*` empties dir: judge it as dir
    if not parts:
        return base.resolve() if base is not None else None
    text = "/".join(parts) if parts != [""] else "/"
    low = text.lower()
    for name in sorted(HOME_VARIABLES, key=len, reverse=True):
        if low == name or low.startswith(name + "/"):
            text = str(home) + text[len(name):]
            break
    if re.search(r"\$|%\w+%", text):
        return None  # an arbitrary variable: not resolvable from the string
    msys = _MSYS.match(text)
    if os.name == "nt" and msys:
        text = f"{msys.group(1)}:/{(msys.group(2) or '/').lstrip('/')}"
    if os.name == "nt" and re.fullmatch(r"[a-z]:", text, re.IGNORECASE):
        text += "/"
    path = Path(text)
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(path))


def _contains(outer: Path, inner: Path) -> bool:
    """`inner` is `outer` or lies under it (case-insensitive on Windows)."""
    a, b = os.path.normcase(str(outer)), os.path.normcase(str(inner))
    return b == a or b.startswith(a.rstrip("\\/") + os.sep)


def _protected(target: Path, workspace: Path, home: Path) -> str | None:
    home = home.resolve()
    if target.parent == target:
        return "a drive root"
    if _contains(target, home):
        return "the user profile root (or a folder containing it)"
    if os.path.normcase(str(target.parent)) == os.path.normcase(str(home.parent)):
        return "a user profile root"
    for name in HARNESS_DIRS:
        if _contains(target, home / name):
            return f"~/{name} (or a folder containing it)"
    workspace = workspace.resolve()
    if _contains(target, workspace) and not _contains(workspace, target):
        return "a folder containing the workspace"
    if (target / ".git").is_dir():
        return "a git repository root"
    return None
