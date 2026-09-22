"""Shell tab-completion for passthrough `ros2`/`colcon` commands.

The obvious obstacle (see README's former "Known limitations") is that
completion has to survive the `docker exec` boundary: bash's completion
machinery runs on the host, but the actual subcommand tree (topic names,
node names, `ros2`'s own argparse structure) only exists inside the
container. This works because `ros2` and `colcon` are both instrumented
with the `argcomplete` package already (confirmed by inspecting a real
image: `register-python-argcomplete3 ros2`/`colcon` exist, and more
importantly `ros2`/`colcon`'s own entry points call
`argcomplete.autocomplete()` unconditionally at startup) -- so rosman
doesn't need to reimplement any completion logic, just relay the protocol:

  1. The installed bash/zsh function (`rosman completion bash`/`zsh`)
     calls `rosman __complete <words...>` with the passthrough words typed
     so far (properly tokenized already by bash, so no quoting to worry
     about on this side).
  2. `rosman __complete` reconstructs the equivalent `ros2 ...`/`colcon
     ...` command line (mirroring `dispatch.dispatch_passthrough`'s own
     rule), and runs it *inside* the workspace container with the
     environment variables argcomplete's protocol expects
     (`_ARGCOMPLETE=1`, `COMP_LINE`, `COMP_POINT`, ...). The instrumented
     program then prints IFS-separated candidates to fd 8 instead of
     actually running, and exits.
  3. Candidates are relayed back to stdout, one per line, for the shell
     function to feed into `COMPREPLY`.

This must never trigger a container build/start -- pressing Tab is not a
"start my workspace" action -- and must never hang the shell, so the
container-running check and the docker exec both run with short timeouts,
and every failure mode (no config, no container, container not running,
docker error, timeout) just yields zero candidates rather than an error.

This fires on every keystroke of a passthrough completion, so it avoids
docker-py and goes straight to a single `docker exec`. Docker itself rejects
a missing or stopped container; an earlier `docker inspect` only added a
round trip and a race. The entry point also avoids importing the full CLI
for `__complete`. `ros2`/`colcon`'s own CLI
startup (~350-450ms, confirmed by measuring it directly) still dominates
total latency and is outside rosman's control -- native `ros2 <TAB>` has
the same cost.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess

from rosman.config import RosmanConfig
from rosman.dispatch import CONTAINER_WORKSPACE_PATH
from rosman.naming import container_name

_ARGCOMPLETE_IFS = "\013"
_COMPLETE_TIMEOUT_SECONDS = 3

RESERVED_COMMAND_NAMES = (
    "init up down status config prune rebuild doctor shell push completion help"
)

# Not "reserved" in the sense above -- these *do* get containerized, just
# with their own first-word rule (verbatim, not ros2-prefixed) instead of
# being reserved-word-excluded. Still need to be in the static first-word
# candidate list alongside the reserved ones though: a container round
# trip for a single typed letter wouldn't ever suggest "colcon"/"rosdep"
# themselves, since neither is a real `ros2` subcommand.
PASSTHROUGH_TOOL_NAMES = ("colcon", "rosdep")

BASH_SCRIPT = r"""# rosman shell completion -- add to ~/.bashrc:
#   eval "$(rosman completion bash)"
_rosman_complete() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    local IFS=$'\013'
    if [[ $COMP_CWORD -eq 1 ]]; then
        local dynamic
        dynamic=$(rosman __complete "$cur" 2>/dev/null)
        local reserved
        IFS=$' \t\n' reserved=$(compgen -W "__RESERVED__ __TOOLS__" -- "$cur")
        COMPREPLY=()
        IFS=$'\n'
        [[ -n "$reserved" ]] && COMPREPLY+=( $reserved )
        IFS=$'\013'
        [[ -n "$dynamic" ]] && COMPREPLY+=( $dynamic )
        # `ros2 doctor` is a real ros2 subcommand that happens to collide
        # with rosman's own reserved "doctor" -- dedupe rather than show it twice.
        IFS=$'\n' COMPREPLY=( $(printf '%s\n' "${COMPREPLY[@]}" | sort -u) )
        return
    fi
    # Offset-only slice (no length): completion is end-of-line only (see
    # module docstring), so COMP_CWORD is always the last index already --
    # and a bash-style "offset:length" slice with a bare variable name in
    # the length position (not a numeric literal) makes zsh's parser
    # mistake the second ":" for the start of a history-modifier chain,
    # even under bashcompinit. Confirmed: `${COMP_WORDS[@]:1:COMP_CWORD}`
    # fails with "unrecognized modifier `C'" under a real zsh 5.9.
    local words=("${COMP_WORDS[@]:1}")
    local out
    out=$(rosman __complete "${words[@]}" 2>/dev/null)
    COMPREPLY=()
    [[ -n "$out" ]] && COMPREPLY=( $out )
}
complete -F _rosman_complete rosman
""".replace("__RESERVED__", RESERVED_COMMAND_NAMES).replace(
    "__TOOLS__", " ".join(PASSTHROUGH_TOOL_NAMES)
)

_ZSH_BODY = r"""    local cur=${words[CURRENT]}
    local -a candidates dynamic args
    args=("${words[@]:1}")

    dynamic=("${(@f)$(rosman __complete "${args[@]}" 2>/dev/null)}")
    if (( CURRENT == 2 )); then
        candidates=(__RESERVED__ __TOOLS__ "${dynamic[@]}")
    else
        candidates=("${dynamic[@]}")
    fi

    # Preserve order while removing the one collision currently possible
    # at the first word (`doctor` is both rosman's command and ros2's).
    typeset -U candidates
    compadd -- "${candidates[@]}"
""".replace("__RESERVED__", RESERVED_COMMAND_NAMES).replace(
    "__TOOLS__", " ".join(PASSTHROUGH_TOOL_NAMES)
)

# Printed for source/wheel installs whose package manager cannot place a
# completion file in the shell's standard lookup path. Package-manager
# installs use PACKAGED_ZSH_SCRIPT below and therefore start no rosman
# process while a new shell is loading.
ZSH_SCRIPT = (
    """# rosman shell completion -- add to ~/.zshrc:
#   eval "$(rosman completion zsh)"
autoload -Uz compinit && compinit
_rosman() {
"""
    + _ZSH_BODY
    + """}
compdef _rosman rosman
"""
)

# zsh autoloads this file as the `_rosman` completion function, so its
# contents are the function body itself rather than another declaration.
PACKAGED_ZSH_SCRIPT = "#compdef rosman\n" + _ZSH_BODY.lstrip()


def build_inner_command(words: list[str]) -> list[str] | None:
    """Mirrors `dispatch.dispatch_passthrough`'s own rule for turning
    passthrough words into a `ros2`/`colcon`/`rosdep` command line. Returns
    None for an empty/reserved first word, which the shell function's
    static candidate list already covers -- no container round trip
    needed."""
    if not words or words[0] in RESERVED_COMMAND_NAMES.split():
        return None
    if words[0] in PASSTHROUGH_TOOL_NAMES:
        return list(words)
    return ["ros2", *words]


def complete(config: RosmanConfig, words: list[str]) -> list[str]:
    """Best-effort completion candidates for the given passthrough words.
    Never raises -- any failure just means no completions this time."""
    inner = build_inner_command(words)
    if inner is None:
        return []
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        return []
    inner_line = shlex.join(inner)
    relay = (
        f"COMP_LINE={shlex.quote(inner_line)} COMP_POINT={len(inner_line)} "
        f"_ARGCOMPLETE=1 _ARGCOMPLETE_IFS=$'\\013' COMP_TYPE=9 "
        f"{inner[0]} 8>&1 9>&2 1>/dev/null 2>/dev/null"
    )
    argv = [
        docker_bin,
        "exec",
        "-w",
        CONTAINER_WORKSPACE_PATH,
        container_name(config.workspace_root),
        "bash",
        "-lc",
        relay,
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=_COMPLETE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    output = result.stdout.decode(errors="replace")
    return [word for word in output.split(_ARGCOMPLETE_IFS) if word]
