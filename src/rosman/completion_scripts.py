"""Static shell-completion scripts.

Deliberately import-free: `rosman completion bash|zsh` runs on every new
shell when it's eval'd from an rc file, so it must not pay for importing
config parsing, the CLI, or anything else.
"""

from __future__ import annotations

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
# Frameworks (oh-my-zsh, etc.) usually ran compinit already; a second run
# costs tens of milliseconds on every shell start.
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }
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
