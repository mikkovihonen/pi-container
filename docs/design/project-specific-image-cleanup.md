# Project-Specific Image Cleanup — Design

## Problem

When a workspace has dependency definition files (`.pi-container/dependencies/root/commands.sh`), `run.py` builds a **project-specific** agent image with the project's setup scripts baked in. Each new build produces a new image tagged `pi-coding-agent-<hash>.local`.

**There is no mechanism to remove older project-specific images.** The lifecycle is:

```
build.sh          →  pi-coding-agent:local  (shared, rebuilt via build.sh only)
run.sh (first)    →  pi-coding-agent-a1b2c3d4e5f6g7h8.local  (project-specific)
run.sh (modify)   →  pi-coding-agent-z9y8x7w6v5u4t3s2.local  (new tag, old one orphaned)
run.sh (modify)   →  pi-coding-agent-q1w2e3r4t5y6u7i8.local  (another orphan)
...
```

Over time this **leaks disk space** on the host — each image is often several GB, and the old ones are never garbage-collected. Worse, there is **no way to identify which images belong to which project**, making manual cleanup painful:

- Image tags are opaque (`pi-coding-agent-<hash>.local`) — no project name, no project directory.
- No metadata links an image back to the workspace that produced it.
- The tag pattern `pi-coding-agent-*` collides with the shared image prefix, so a blanket `docker image prune` or `podman system prune` could accidentally remove images that should be kept.

## Root Cause Analysis

The current implementation has three gaps:

### 1. No project identity in image tags or labels

`_resolve_agent_image()` in `run.py` computes the tag as:

```python
project_image_tag = f"pi-coding-agent-{image_hash}.local"
```

The `image_hash` encodes the content of `Containerfile` + `entrypoint.sh` + `root/commands.sh`, but **not the project's identity**. Two different projects with identical definition files produce images with the **same** tag, which is technically correct (same content → same image), but it means a single tag can be shared across projects — and conversely, there's no way to find all images that *belong* to a specific project.

The only label set on the image is `pi-container.hash` (the content hash). There is no `pi-container.project.*` label.

### 2. No replacement semantics

Container runtimes (Docker, Podman) don't have an atomic "replace image" operation. `docker build --tag foo:bar` creates a new image; if `foo:bar` already exists, it becomes an intermediate layer or gets overwritten only in specific cases. There is **no code path** that calls `image rm` or `rmi` anywhere in the codebase — a grep for `image rm`, `image.remove`, `prune`, or `delete.*image` returns nothing.

### 3. No tracking / registry

The code does not persist any record of which project-specific images exist. The only reference is the tag itself, and it's computed freshly each run. There is no manifest, no database, no sidecar file tracking: "project X at time T used image Y with hash Z."

## Design: Project-Scoped Image Lifecycle

The solution addresses all three gaps with three coordinated changes: **renamed tags with project identity**, **labels that enable discovery**, and **pre-build cleanup of stale images**.

### 1. New Image Tag Format

**Current:** `pi-coding-agent-<image-hash>.local`

**Proposed:** `pi-container-project-<project-hash>-<image-hash>.local`

Where:
| Component | Source | Length |
|-----------|--------|--------|
| `project-hash` | `sha256(project_dir)`[:10] (same hash used for proxy/network names) | 10 hex chars |
| `image-hash` | Current `_compute_image_hash()` content digest | 16 hex chars |

Example: `pi-container-project-a1b2c-d4e5f6a7b8c9d0e1.local`

**Why this matters:**
- The `project-hash` component makes it possible to **enumerate all images for a given project** via `docker/podman image ls --filter "label=pi-container.project.hash=<hash>"` or by tag glob.
- The `image-hash` component makes it possible to **detect stale images** by comparing the label against the current content hash.
- The prefix `pi-container-project-` is **distinct from** the shared image prefix `pi-coding-agent`, so `docker image prune` will never accidentally touch these.

### 2. Image Labels

Add five labels to the Containerfile via build args:

```dockerfile
ARG PROJECT_HASH=""
ARG PROJECT_PATH=""
ARG IMAGE_HASH=""
ARG BUILD_TIMESTAMP=""

LABEL pi-container.project.hash="${PROJECT_HASH}"
LABEL pi-container.project.path="${PROJECT_PATH}"
LABEL pi-container.hash="${IMAGE_HASH}"
LABEL pi-container.build.time="${BUILD_TIMESTAMP}"
LABEL pi-container.type="project"
```

The existing `pi-container.hash` label is **preserved** (backwards compatible with the current cache-invalidation logic). The new labels add:

| Label | Purpose |
|-------|---------|
| `pi-container.type` | Distinguishes project images from shared images (`project` vs `shared`). Enables `--filter "label=pi-container.type=project"` to find all project images system-wide. |
| `pi-container.project.hash` | The project's identity hash (first 10 chars of `SHA-256(project_dir)`). Enables finding all images that belong to a specific workspace. |
| `pi-container.project.path` | **Absolute path of the project directory at build time.** Enables detection of orphaned images when a project directory is deleted — if the stored path no longer exists, the image can be safely removed. |
| `pi-container.build.time` | ISO 8601 timestamp. Enables sorting by age and identifying the most recent image for a project. |

### 3. Pre-Build Cleanup: `_cleanup_stale_project_images()`

Before building a new project-specific image, the following sequence runs:

```
for each project-specific image on the host:
    if its pi-container.project.hash != current_project_hash:
        skip  (belongs to a different project — do not touch)
    if its pi-container.hash == current_image_hash:
        skip  (this IS the new image or an identical cached image — no build needed)
    else:
        remove the old image  (stale — content changed)

Additionally, detect and remove orphaned images from deleted projects:
    if its pi-container.project.path label is set and the path no longer exists:
        remove the image  (orphan — project was deleted)
```

This is implemented as two new functions in `run.py`:

```python
def _cleanup_stale_project_images(
    runtime: str,
    project_dir: Path,
    project_hash: str,
    new_hash: str,
) -> list[str]:
    """Find and remove project-specific images that are stale for this project.

    Returns the list of removed image tags. Skips images belonging to other
    projects or images that already match the new content hash.

    Strategy:
      1. Enumerate all images with label pi-container.type=project.
      2. Filter to those whose pi-container.project.hash matches this project.
      3. Among those, remove any whose pi-container.hash != new_hash.
      4. If no image matches new_hash, the build will create it.
      5. If an image already matches new_hash, skip the build entirely (cache hit).
    """


def _cleanup_orphaned_project_images(runtime: str) -> list[str]:
    """Remove project images whose source project no longer exists.

    An image is considered orphaned if:
    - It has no `pi-container.project.path` label (older images, unverifiable), OR
    - Its `pi-container.project.path` label points to a path that doesn't exist.

    Only images with a path label pointing to an **existing** directory are kept.

    Returns the list of removed image tags.
    """
```

### 4. Updated Build Flow in `run.py::main()`

**Current flow:**

```python
agent_image_tag, is_project_specific = _resolve_agent_image(PROJECT_DIR)
if is_project_specific:
    label_hash = _compute_image_hash(PROJECT_DIR)
    if not _image_is_current(PROJECT_DIR, agent_image_tag, label_hash):
        build_project_image(...)
    else:
        logger.info("Using cached project-specific image")
```

**New flow:**

```python
agent_image_tag, is_project_specific = _resolve_agent_image(PROJECT_DIR)
if is_project_specific:
    current_hash = _compute_image_hash(PROJECT_DIR)
    project_hash = _project_scope(PROJECT_DIR)[0].split("pi-proxy-")[1]
    # 1. Cleanup orphaned images from deleted projects.
    orphaned = _cleanup_orphaned_project_images(CONTAINER_RUNTIME)
    if orphaned:
        logger.info(f"Removed {len(orphaned)} orphaned project image(s): {', '.join(orphaned)}")
    # 2. Cleanup stale images for this project BEFORE deciding to build.
    removed = _cleanup_stale_project_images(
        CONTAINER_RUNTIME, PROJECT_DIR, project_hash, current_hash,
    )
    if removed:
        logger.info(f"Removed {len(removed)} stale project image(s): {', '.join(removed)}")
    # 3. Now check if the current image is already up-to-date (post-cleanup).
    if not _image_is_current(PROJECT_DIR, agent_image_tag, current_hash):
        logger.info(f"Building project-specific agent image: {agent_image_tag}")
        build_project_image(
            CONTAINER_RUNTIME,
            root_commands_path,
            pi_commands_path,
            agent_image_tag,
            current_hash,
            project_hash=project_hash,
            project_path=str(PROJECT_DIR.resolve()),
            build_timestamp=now_iso(),
        )
    else:
        logger.info(f"Using cached project-specific image: {agent_image_tag}")
```

### 5. Updated `build_project_image()` in `build.py`

```python
def build_project_image(
    runtime: str,
    root_commands_path: str,
    pi_commands_path: str,
    image_tag: str,
    label_hash: str,
    project_hash: str = "",
    project_path: str = "",
    build_timestamp: str = "",
) -> None:
    """Build a project-specific agent image with baked-in command scripts.

    New optional args:
        project_hash:      Project identity hash → set as pi-container.project.hash label.
        project_path:      Absolute project directory path → set as pi-container.project.path label.
        build_timestamp:   ISO 8601 timestamp → set as pi-container.build.time label.
    """
    cmd = [
        runtime, "build",
        "--build-context", f"root_commands_path={Path(root_commands_path).parent}",
        "--build-arg", f"ROOT_COMMANDS_PATH={Path(root_commands_path).name}",
        "--build-arg", f"LABEL_HASH={label_hash}",
        "--build-arg", f"PROJECT_HASH={project_hash}",
        "--build-arg", f"PROJECT_PATH={project_path}",
        "--build-arg", f"BUILD_TIMESTAMP={build_timestamp}",
        "--label", "pi-container.type=project",
        "--tag", image_tag,
        "--file", str(REPO_ROOT / "pi-coding-agent" / "Containerfile"),
        str(REPO_ROOT),
    ]
    ...
```

### 6. Updated Containerfile

```dockerfile
ARG PI_UID=1000
ARG PI_GID=1000

RUN userdel --remove node 2>/dev/null || true \
 && groupdel node 2>/dev/null || true \
 && groupadd --gid ${PI_GID} pi \
 && useradd --uid ${PI_UID} --gid ${PI_GID} --create-home --shell /bin/bash pi

ENV NODE_USE_SYSTEM_CA=1
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

RUN mkdir -p /usr/local/share/ca-certificates/extra
COPY --from=pi-coding-agent-proxy:local /home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.pem
RUN openssl x509 -in /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.pem \
    -inform PEM -out /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.crt \
    && update-ca-certificates
RUN npm set cafile /usr/local/share/ca-certificates/extra/mitmproxy-ca-cert.pem

ARG ROOT_COMMANDS_PATH
ARG LABEL_HASH
ARG PROJECT_HASH=""
ARG PROJECT_PATH=""
ARG BUILD_TIMESTAMP=""

COPY --from=root_commands_path ${ROOT_COMMANDS_PATH} /root/commands.sh
RUN chmod +x /root/commands.sh && /root/commands.sh

WORKDIR /workspace

LABEL pi-container.hash="${LABEL_HASH}"
LABEL pi-container.project.hash="${PROJECT_HASH}"
LABEL pi-container.project.path="${PROJECT_PATH}"
LABEL pi-container.build.time="${BUILD_TIMESTAMP}"
LABEL pi-container.type="project"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

### 7. New Helper: `_list_project_images()`

A helper to enumerate images for a given project:

```python
def _list_project_images(runtime: str, project_hash: str) -> list[tuple[str, str]]:
    """List all project-specific images for a given project hash.

    Returns list of (tag, content_hash) tuples. Uses docker/podman image ls
    with label filters.
    """
    images = []
    try:
        result = subprocess.run(
            [runtime, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}",
             "--filter", f"label=pi-container.type=project"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        for tag in result.stdout.strip().splitlines():
            label = _get_image_label(tag, "pi-container.project.hash")
            if label == project_hash:
                content_hash = _get_image_label(tag, "pi-container.hash")
                images.append((tag, content_hash or ""))
    except Exception as e:
        logger.warning(f"Could not list project images: {e}")
    return images
```

### 8. New Helper: `_remove_image()`

A helper to remove a single image:

```python
def _remove_image(runtime: str, image_tag: str) -> bool:
    """Remove a container image by tag. Returns True on success."""
    try:
        result = subprocess.run(
            [runtime, "image", "rm", image_tag],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"Removed image: {image_tag}")
            return True
        else:
            logger.warning(f"Could not remove image {image_tag}: {result.stderr.strip()}")
            return False
    except Exception as e:
        logger.warning(f"Could not remove image {image_tag}: {e}")
        return False
```

### 9. Optional: Standalone Cleanup Command (Future)

A future enhancement (not part of v1) would add a `cleanup` subcommand or flag:

```bash
# List all pi-container project images
./run.sh --list-images

# Clean up images for projects that no longer exist
./run.sh --cleanup

# Force rebuild all project images
./run.sh --rebuild-images
```

The `--cleanup` variant would:
1. List all images with `pi-container.type=project`
2. For each, read `pi-container.project.hash`
3. Check if the corresponding project directory still exists
4. Report orphaned images (project dir gone) and remove them with confirmation

---

## Backwards Compatibility

- The `pi-container.hash` label is **preserved** — current cache invalidation logic works unchanged.
- Old images (tagged `pi-coding-agent-<hash>.local`) without the new labels are **not touched** by cleanup. They remain as orphans until manually removed or until a future cleanup migration pass identifies them by their `pi-container.hash` label.
- The shared image tag `pi-coding-agent:local` is **unaffected**.

## Migration Path for Existing Orphaned Images

Existing `pi-coding-agent-<hash>.local` images that have a `pi-container.hash` label but no `pi-container.project.hash` label are treated as **unidentified**. A future cleanup phase could:

1. Scan all `pi-coding-agent-<hash>.local` images with `pi-container.hash` labels.
2. For each, compare the hash against all known project content hashes (by running `_compute_image_hash()` on every workspace's `.pi-container/dependencies/`).
3. If a match is found → relabel with the project's `pi-container.project.hash`.
4. If no match → flag as orphan for manual review.

This migration is **out of scope for v1**.

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Build fails mid-way | Old image remains (cleanup happens *before* build, not after). No data loss. |
| Two projects with identical definition files | Both produce the same `image-hash`; if project-hashes differ, they get distinct tags. Correct. |
| Same project, same definition files (re-run) | `_image_is_current()` returns True → no build, no cleanup. Correct. |
| Project directory deleted between builds | `_cleanup_orphaned_project_images()` detects the missing path and removes all images for that project. **Fully automatic cleanup.** |
| Project moved to new path | Old images retain the old path label → detected as orphaned and removed. New images get the new path label. Correct. |
| Concurrent runs of the same project | Both compute the same hashes. The first build succeeds; the second finds the image already current. Cleanup finds no stale images. Correct. |
| Concurrent runs of different projects | Each cleans up only its own project's images. No cross-contamination. Correct. |
| Container runtime not available | All subprocess calls are guarded by try/except → warning logged, no build, no crash. |
| Image without `pi-container.project.path` label | Removed by orphan cleanup — unverifiable source, treated as orphan. |

---

## Open Questions

1. **Should we also clean up the shared image (`pi-coding-agent:local`) when `build.sh` runs?** This is a separate concern — `build.sh` already rebuilds the shared image, but it doesn't remove the old one. Could add similar cleanup there in v2.

2. **Should `pi/commands.sh` changes trigger a new image?** Currently they don't (they run at runtime, not bake into the image). This is by design — but if users expect `pi/commands.sh` changes to affect the image, the hash function would need updating. Out of scope.

3. **What about the `pi-coding-agent-proxy:local` image?** Same issue exists there (no cleanup after `build.sh`), but the proxy image is rebuilt every time `build.sh` runs (not per-project), so the accumulation rate is much lower. Can address in v2 if needed.

4. **Should we add a `--dry-run` flag to `_cleanup_stale_project_images()` for safety?** Useful for debugging and for the future `--cleanup` command. Could be a parameter on the function.

| File | Change |
|------|--------|
| `src/run.py` | New `_cleanup_stale_project_images()`, `_cleanup_orphaned_project_images()`, `_list_project_images()`, `_remove_image()`, `now_iso()`. Updated `_resolve_agent_image()` to return new tag format. Updated `main()` flow to call both cleanup functions. |
| `src/build.py` | `build_project_image()` gains `project_hash`, `project_path`, and `build_timestamp` params; passes them as build args + labels. |
| `pi-coding-agent/Containerfile` | Add `ARG PROJECT_HASH`, `ARG PROJECT_PATH`, `ARG BUILD_TIMESTAMP`; add four new `LABEL` directives (including `pi-container.project.path`). |
| `docs/configuration.md` | Document new labels (including `pi-container.project.path`), new tag format, cleanup behavior, and orphan detection. |
| `src/tests/test_build.py` | Add tests for new `build_project_image()` args and labels. |
| `src/tests/test_run.py` | Add tests for `_cleanup_stale_project_images()`, `_list_project_images()`, `_remove_image()`, new tag format. |

---

## Addendum: Corrections Found in Practice

Three details of the design above turned out to be wrong once it was running. All
three are fixed in the code; this section records why, so the design document is not
read as current.

### 1. `pi-container.type` must not live in the Containerfile

The design puts `LABEL pi-container.type="project"` in
`pi-coding-agent/Containerfile`. But that one Containerfile builds **two** different
things: the shared base image (`build_agent()`, which passes none of the label ARGs)
and the per-project images (`build_project_image()`, which passes all of them). The
hardcoded label stamped the shared base — and every untagged predecessor of it — as
`type=project` with blank values for every other label, so the orphan pass could not
tell the base apart from a real project's image.

Each builder now sets the label itself via `--label` on the command line
(`type=shared` for the base, `type=project` for project images). A useful side
effect: a CLI `--label` lands only on the final image, never on build intermediates,
so an in-flight build cannot be mistaken for an orphan by a concurrent run.

### 2. Enumerate images by ID, not by `Repository:Tag`

`_list_project_images()` and `_cleanup_orphaned_project_images()` both listed images
as `{{.Repository}}:{{.Tag}}`. Every rebuild that moves a tag leaves the previous
image untagged, and podman renders an untagged image's name as the literal string
`<none>:<none>` — which is not a valid image reference:

```
Error: parsing reference "<none>:<none>": invalid reference format
```

Both the inspect and the remove failed on it, so untagged images could never be
reclaimed and produced a pair of warnings on every startup. Both functions now
enumerate `{{.ID}}\t{{.Repository}}:{{.Tag}}`, operate on the ID, and use the name
for log messages only.

### 3. A blank path label is not the same as a present one

The orphan rule — "remove if `pi-container.project.path` points at a path that no
longer exists" — was implemented as `Path(stored_path).exists()`. For a blank label
that is `Path("")`, which is `PosixPath(".")`, which always exists. Blank-labelled
images were therefore kept forever rather than treated as unverifiable. The blank
case is now checked explicitly, alongside the missing-label case.

### Protected images

Because images built before correction 1 are still on disk carrying the old label,
`_cleanup_orphaned_project_images()` also refuses outright to remove anything in
`_PROTECTED_IMAGE_TAGS` (the shared base, proxy and builder images), whatever their
labels say. The comparison ignores a leading `localhost/`, which podman prepends to
locally-built images and docker does not.

### 4. An image a container holds open is not reclaimable

Neither cleanup pass checked whether a container was still using an image before
trying to remove it. The removal fails:

```
Error: image used by 29703bda…: image is in use by a container:
consider listing external containers and force-removing image
```

This is not an edge case. Concurrent sessions in one workspace are supported, and
the design's own edge-case table calls the concurrent-same-project case "correct" —
but it only considered two runs racing to *build*, not one run cleaning up while an
earlier session is still running on the image it started from. Change a definition
file, start a second session in the same workspace, and the stale-image pass targets
the image the first session is running on. It warned on every start for as long as
that session lived.

Both passes now consult `_images_in_use()` — the image IDs of all containers, running
or stopped — and skip those images with an INFO line. The image is reclaimed by a
later run, once the container is gone. The check is made only after an image has been
established as a removal candidate, so images belonging to live projects stay silent
rather than logging a skip on every startup.

`_cleanup_orphaned_nested_volumes()` had the same gap and got the same treatment,
via `_unused_volumes()`. The query is inverted there because that is the form podman
answers directly: `ps --format {{.Mounts}}` reports mount *destinations*, which cannot
be mapped back to a volume name, while `volume ls --filter dangling=true` lists
exactly the volumes nothing references. A merely created, never-started container is
enough to keep a volume off that list, which matches the `ps --all` semantics used for
images. A failed query returns None rather than an empty set, so "cannot tell" falls
back to attempting the removal instead of silently skipping every volume.

The blank-path correction (3) applied to the volume pass too — it shared the
`Path("")` bug verbatim.
