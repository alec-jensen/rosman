# Shell tab-completion

The apt/dnf/pacman packages install completion files into bash and zsh's
standard lookup paths. Restart the shell after installing or upgrading;
there is no shell-rc line to add. If you configured an older release with
`eval "$(rosman completion ...)"`, remove that line — leaving it is harmless,
but starts an unnecessary process on every new shell.

For source or wheel installs, register completion manually:

```sh
echo 'eval "$(rosman completion bash)"' >> ~/.bashrc
# or, for zsh:
echo 'eval "$(rosman completion zsh)"' >> ~/.zshrc
```

Restart your shell (or `source ~/.bashrc`/`~/.zshrc`), then tab-complete
`rosman` calls exactly as if you were running `ros2`/`colcon` natively:

```sh
$ rosman topic ec<TAB>
$ rosman topic echo
$ rosman topic echo <TAB><TAB>
/parameter_events  /rosout
```

## How it works

`ros2` and `colcon` are already instrumented with Python's `argcomplete`
package — their own entry points call it unconditionally at startup. rosman
doesn't reimplement any of that completion logic; it relays the same
protocol through the `docker exec` boundary: the installed shell function
runs a hidden `rosman __complete` that reconstructs the equivalent `ros2
...`/`colcon ...`/`rosdep ...` command line, runs it inside your
workspace's container with the environment variables `argcomplete`
expects, and forwards the candidates back.

## Limitations

- Only works while the workspace container is already running (`rosman
  up`, or after any passthrough command has auto-started it). Pressing
  Tab never starts a container by itself.
- Completion is end-of-line only — no mid-line editing awareness.
- The very first word after `rosman` merges rosman's own reserved commands
  (`doctor`, `shell`, ...) with `colcon`/`rosdep` and whatever `ros2`
  itself suggests, since rosman can't tell which one you're typing from a
  short prefix alone.
- `rosdep` itself isn't `argcomplete`-instrumented (unlike `ros2`/
  `colcon`) — confirmed directly against a real container: it doesn't
  respond to the completion protocol at all. So `rosman rosdep` completes
  as a first word, but its own subcommands/flags (`update`, `install`,
  `--from-paths`, ...) don't tab-complete beyond that. Nothing rosman can
  fix short of writing its own `rosdep` completer, which would mean
  reimplementing part of a tool rosman otherwise just forwards to.
