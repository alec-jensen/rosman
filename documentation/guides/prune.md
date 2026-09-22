# Cleaning up: rosman prune

```sh
rosman prune
```

Every change to `rosman.yml`/`rosman.lock` — or a rosman upgrade that
touches the image build template — produces a new, distinctly
content-hash-tagged image. Nothing removes the old one automatically, so
these accumulate over time. On a machine used for real development, this
adds up fast: several gigabytes per stale image, times however many
config changes and rebuilds have happened.

`rosman prune` removes rosman-built images that no longer have **any**
existing container referencing them — running or stopped — regardless of
*why* they became orphaned:

- A config change produced a new hash-tagged image, and the container
  was rebuilt against it, leaving the old image tag behind with nothing
  using it.
- The workspace itself was deleted from disk entirely, but its
  container/image never got explicitly removed first.

```sh
$ rosman prune
Found 6 unreferenced rosman image(s), 7.2GB total:
  - rosman/humble-00a95c48:1b8c0353fb15 (1.4GB)
  - rosman/humble-3fb2f057:475f136f54fb (1.4GB)
  ...
Remove these images? [y/N] y
Removed 6 image(s), reclaimed 7.2GB.
```

## Why it's safe

`rosman prune` only ever removes an image with **zero** container
references — an image any container (even a stopped one you might still
want) still points at is never touched. This is the same standard as
`docker image prune`'s own dangling-image cleanup, just scoped to
rosman-built images specifically (identified by a build-time label, with
a tag-pattern fallback for images built before that label existed) rather
than truly untagged ones — which is why `rosman prune` finds real,
reclaimable space that plain `docker system prune` won't, since a
rosman-built image always has a real tag and is never "dangling" in
Docker's own sense.

## Scope

It's a **global** operation, not scoped to the current workspace or
directory — a deleted workspace's orphaned images are exactly as safe (and
worth) cleaning up as a rebuilt one's, and there's no reliable way to
enumerate "only this workspace's old images" that would also catch the
deleted-workspace case.

## Non-interactive use

```sh
rosman prune --yes   # or -y
```

Without `-y`/`--yes`, an interactive session prompts before removing
anything; a non-interactive session (no tty — a script or CI) just prints
what it would remove and exits without removing anything, so passing
`--yes` is required there.
