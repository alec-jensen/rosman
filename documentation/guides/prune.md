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

`rosman prune` removes older rosman-built images while preserving the
newest image for each existing workspace, even if no container uses it
yet. It also preserves every image referenced by a running or stopped
container.

A config change may leave an older image with no container. Images from
a workspace deleted from disk can also be cleaned up. Rosman identifies
workspaces from image labels and, for images created before those labels
existed, from their tags. When an older image's workspace path cannot be
recovered, rosman keeps the newest image in that tag group to avoid
deleting a workspace's only available image. Shared registry image tags
receive the same conservative treatment.

```sh
$ rosman prune
Found 6 old, unused rosman image(s), 7.2GB total:
  - rosman/humble-00a95c48:1b8c0353fb15 (1.4GB)
  - rosman/humble-3fb2f057:475f136f54fb (1.4GB)
  ...
Remove these images? [y/N] y
Removed 6 image(s), reclaimed 7.2GB.
```

## Why it's safe

The candidate list excludes images used by any existing container and
each workspace's latest image. Rosman removes only its own managed
images, identified by a build-time label or an older rosman tag pattern.
It previews candidates and their sizes before asking for confirmation.

## Scope

Pruning is global across workspaces. For labeled local images, deleting
a workspace makes all of its unreferenced images eligible. Legacy and
shared registry tags may retain one image when Docker metadata cannot
reliably identify the workspace path.

## Non-interactive use

```sh
rosman prune --yes   # or -y
```

Without `-y`/`--yes`, an interactive session prompts before removing
anything; a non-interactive session (no tty — a script or CI) just prints
what it would remove and exits without removing anything, so passing
`--yes` is required there.
