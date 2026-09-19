# Team-shared images

If a project's image takes a while to build (a heavy `setup_script`, lots
of `extra_apt_packages`), you don't want every teammate rebuilding it
independently. rosman still builds the image itself —
`registry_image` just controls where the *result* is cached, so a team
shares one build instead of everyone repeating it.

```yaml
registry_image: ghcr.io/my-team/my-project   # no tag -- rosman appends its own
```

```sh
rosman push   # builds (if needed) and pushes -- once, after `docker login ghcr.io`
rosman up     # teammates: pulls the pushed image instead of building locally
```

`rosman up` (and the auto-start on any passthrough command) tries a pull
first whenever `registry_image` is set, falling back to a local build only
if nothing's been pushed yet or the registry isn't reachable. Registry
auth is your own `docker login` — rosman doesn't manage credentials.

!!! note "This is not \"bring your own image\""
    rosman always builds the image itself from `rosman.yml`
    (`ros:<distro>`, or `base_image` + `setup_script`). `registry_image`
    only changes where the *result* is cached — you can't point rosman at
    an arbitrary pre-built image and skip its own build. That would
    reintroduce exactly the "everyone manages their own image slightly
    differently" problem rosman exists to remove.

## Why file permissions still work

The image bakes in a fixed internal identity (not whoever happened to
build it) — each machine's actual host UID/GID is applied at container
*runtime*, so bind-mounted workspace files still come out correctly owned
regardless of who built the shared image. This is also what makes the
image's content-hash tag reusable across different builders in the first
place: if the image baked in the builder's own UID, two teammates with
different UIDs building the identical `rosman.yml` would get two different
image tags, with nothing to actually share.
