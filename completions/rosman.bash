# rosman shell completion -- add to ~/.bashrc:
#   eval "$(rosman completion bash)"
_rosman_complete() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    local IFS=$'\013'
    if [[ $COMP_CWORD -eq 1 ]]; then
        local dynamic
        dynamic=$(rosman __complete "$cur" 2>/dev/null)
        local reserved
        IFS=$' \t\n' reserved=$(compgen -W "init up down status config prune rebuild doctor shell push completion help colcon rosdep" -- "$cur")
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
