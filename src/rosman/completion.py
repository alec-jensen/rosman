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

from rosman.completion_scripts import (  # noqa: F401 - re-exported
    BASH_SCRIPT,
    PACKAGED_ZSH_SCRIPT,
    PASSTHROUGH_TOOL_NAMES,
    RESERVED_COMMAND_NAMES,
    ZSH_SCRIPT,
)
from rosman.config import RosmanConfig
from rosman.dispatch import CONTAINER_WORKSPACE_PATH
from rosman.naming import container_name

_ARGCOMPLETE_IFS = "\013"
_COMPLETE_TIMEOUT_SECONDS = 3


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
