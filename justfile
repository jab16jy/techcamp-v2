# Dev tooling (#139): the static checks plus exactly the tests you name, never the whole
# suite by accident. CI calls the tools directly, so this file and
# .github/workflows/ci.yml must list the same checks; the close task diffs them.
#
# `just` is a system tool, not a project dependency: `uv tool install rust-just`
# (or `cargo install just`, or brew). See AGENTS.md Commands.

set positional-arguments
set export

# One test database per worktree. `server/tests/conftest.py` migrates to head and
# downgrades to base once per session, so two worktrees on one database drop each
# other's tables (#139). Identity is the path of the worktree this justfile lives in.
#
# The identity is derived in the private `db-env` recipe, not in a backtick: `just`
# does not interpolate `{{_worktree}}` inside a backtick string, so a backtick would
# hash the literal text "{{_worktree}}" and every worktree would resolve to the same
# container and port. Recipes read the three values from its output instead.
#
# ponytail: the derivation is four lines of POSIX shell, and no dependency earns its
# place here. A port already bound fails `db-up` loudly rather than sharing a database.
_worktree := justfile_directory()
_runtime := `sh -c 'command -v podman || command -v docker'`
# podman runs the container under SELinux on Linux and needs the bind mount labelled;
# docker rejects the label, so it only rides on the podman side.
_vol_label := `sh -c 'command -v podman >/dev/null 2>&1 && echo ",z" || echo ""'`

# `<port> <container name> <database url>` for this worktree. Read as
# `read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"`.
[private]
[no-exit-message]
db-env:
    #!/usr/bin/env bash
    set -euo pipefail
    # cksum prints the CRC and a byte count, and the CRC is the first field. It is POSIX
    # and deterministic, so the same worktree path always lands on the same port.
    crc=$(printf %s "{{_worktree}}" | cksum)
    crc=${crc%% *}
    port=$((55000 + crc % 10000))
    slug=$(basename "{{_worktree}}" | tr "[:upper:]" "[:lower:]" | sed "s/[^a-z0-9-]/-/g")
    echo "$port techcamp-db-$slug-$crc postgresql+asyncpg://techcamp:techcamp@127.0.0.1:$port/techcamp"

# Listing the recipes is the default. Without this, the first recipe in the file would
# run on a bare `just`, and that one starts a database.

# List the recipes; a bare `just` starts nothing.
default:
    @just --list --unsorted

# The one command to read when two worktrees seem to share a database: which container
# and port this worktree owns, and the URL its tests will use.

# What this worktree resolves to: container, runtime, DATABASE_URL.
[group: 'database']
db-info:
    #!/usr/bin/env bash
    set -euo pipefail
    read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"
    echo "worktree:  {{_worktree}}"
    echo "container: $container"
    echo "port:      $port"
    echo "runtime:   {{_runtime}}"
    echo "DATABASE_URL: $url"

# This worktree's own Postgres, with the extensions `infra/compose.yaml` mounts
# (postgis, timescaledb, vector — ADR-0003). Credentials are the seminar defaults from
# `shared/config.py`: a local emulator nobody authenticates, bound to loopback.

# Start this worktree's database and wait until it takes connections.
[group: 'database']
db-up:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "{{_runtime}}" ]; then
      echo "neither podman nor docker is on PATH; one of them is needed for the test database" >&2
      exit 1
    fi
    read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"
    if [ -n "$({{_runtime}} ps -aq --filter "name=^$container$")" ]; then
      echo "$container already exists; run 'just db-reset' for a clean one" >&2
      exit 1
    fi
    {{_runtime}} run -d --name "$container" \
      -e POSTGRES_USER=techcamp -e POSTGRES_PASSWORD=techcamp -e POSTGRES_DB=techcamp \
      -p 127.0.0.1:$port:5432 \
      -v "{{_worktree}}/infra/postgres/init-extensions.sql:/docker-entrypoint-initdb.d/init-extensions.sql:ro{{_vol_label}}" \
      docker.io/timescale/timescaledb-ha:pg16
    for _ in $(seq 1 30); do
      # -h 127.0.0.1 on purpose: while the image runs its init scripts the entrypoint
      # serves a temporary server on the unix socket only, so a socket check would call
      # the database ready before postgis, timescaledb and vector exist.
      if {{_runtime}} exec "$container" pg_isready -h 127.0.0.1 -U techcamp >/dev/null 2>&1; then
        echo "$container is ready on port $port"
        exit 0
      fi
      sleep 2
    done
    echo "$container did not become ready; read {{_runtime}} logs $container" >&2
    exit 1

# Containers are named after their own worktree path, so this reaches this worktree's
# database and no other.

# Stop this worktree's database and drop its storage.
[group: 'database']
db-down:
    #!/usr/bin/env bash
    set -euo pipefail
    read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"
    if [ -z "$({{_runtime}} ps -aq --filter "name=^$container$")" ]; then
      echo "no $container to remove"
      exit 0
    fi
    # -v drops the anonymous pgdata volume as well, so the next db-up starts empty.
    {{_runtime}} rm -f -v "$container"

# The first thing gate-full does, and the answer to a database left in a bad state by
# a killed run.

# A clean database for this worktree: down, then up.
[group: 'database']
db-reset: db-down db-up

# No database and no tests, so this is what a lane can run on any commit. Everything
# `gate` and `gate-full` do starts here.

# Static checks only: every gate builds on this.
[group: 'gate']
gate-fast:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{_worktree}}/server"
    echo "--- ruff check";        uv run ruff check
    echo "--- ruff format";      uv run ruff format --check
    echo "--- mypy";             uv run mypy
    echo "--- lint-imports";     uv run lint-imports
    # A branched migration history is one `alembic heads` line per head, and no later
    # migration repairs it except a merge revision nobody remembers to write.
    echo "--- alembic heads"
    heads=$(uv run alembic heads | wc -l)
    if [ "$heads" -ne 1 ]; then
      echo "expected exactly 1 Alembic head, found $heads:" >&2
      uv run alembic heads >&2
      exit 1
    fi
    cd "{{_worktree}}/web"
    echo "--- eslint";           npm run lint
    echo "--- tsc";              npm run typecheck

# `server/...` runs pytest from `server/`, `web/...` runs vitest, and anything else is
# already covered by the static checks. No paths means the static checks only (D-T0.1):
# the full suite is `gate-full`, an explicit decision at epic close, never an accident
# of a bare `gate`. Node ids pass through: `just gate server/tests/x/test_y.py::test_z`.
#
# DATABASE_URL is exported from this worktree's own identity and never inherited: a URL
# left over from another checkout would point these tests at a database another
# worktree migrates and drops. Run pytest directly for a database these recipes do not
# own.

# The static checks plus the tests you name, and only those.
[group: 'gate']
gate *paths: gate-fast
    #!/usr/bin/env bash
    set -euo pipefail
    read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"
    export DATABASE_URL="$url"
    for path in "$@"; do
      case "$path" in
        server/*)
          # A connection traceback from pytest says nothing about what to do; the test
          # database is per worktree and this worktree's own container has to be up.
          if [ -z "$({{_runtime}} ps -q --filter "name=^$container$")" ]; then
            echo "this worktree's database ($container) is not running: run 'just db-up' first" >&2
            exit 1
          fi
          echo "--- pytest $path"
          (cd "{{_worktree}}/server" && uv run pytest "${path#server/}")
          ;;
        web/*)
          echo "--- vitest $path"
          (cd "{{_worktree}}/web" && npm test -- --run "${path#web/}")
          ;;
        *)
          echo "--- $path: no test runner; the static checks already ran"
          ;;
      esac
    done

# The run the owner does once per epic, on a database this worktree just recreated, so
# no leaked table from an earlier run can be mistaken for a passing suite (#89).

# Epic close: a clean database, then every check, on purpose.
[group: 'gate']
gate-full: db-reset gate-fast
    #!/usr/bin/env bash
    set -euo pipefail
    read -r port container url <<<"$(just --justfile "{{_worktree}}/justfile" db-env)"
    export DATABASE_URL="$url"
    cd "{{_worktree}}/server"
    echo "--- pytest (full)"; uv run pytest
    cd "{{_worktree}}/web"
    echo "--- vitest (full)"; npm test -- --run
    echo "--- vite build";    npm run build
    echo "--- size budget";   npm run size

# The E8 PR #182 lesson: a middle commit of the lane imported a file the next commit
# added, and gating only HEAD passed. This runs the static checks on every commit,
# oldest first, and stops at the first failure naming its sha.
#
# Each commit is checked out in a detached temporary worktree, so the lane's own tree
# never moves and no commit is rewritten: the shas this prints are the shas the branch
# has. Every checkout is checked against its own dependency graph: uv builds that
# commit's own .venv from its own uv.lock, and `web/node_modules` is installed for a
# commit that changed the lockfile instead of borrowed from the lane, so no commit is
# judged against another commit's dependencies. `gate-fast` is static, so no database
# is involved.

# Static checks on every commit of the lane, oldest first, stopping at the first failure.
[group: 'gate']
gate-lane base="main":
    #!/usr/bin/env bash
    set -euo pipefail
    lane_root="{{_worktree}}"
    shas=$(git -C "$lane_root" rev-list --reverse "{{base}}..HEAD")
    if [ -z "$shas" ]; then
      echo "no commits in {{base}}..HEAD; nothing to gate"
      exit 0
    fi
    checkout=$(mktemp -d)
    # The trap is the safety net; the loop removes each worktree as it goes, so an
    # interrupted run leaves at most one registered worktree behind.
    trap 'git -C "$lane_root" worktree remove --force "$checkout" >/dev/null 2>&1 || true' EXIT
    for sha in $shas; do
      subject=$(git -C "$lane_root" log -1 --format=%s "$sha")
      echo "=== $sha $subject"
      git -C "$lane_root" worktree add --detach "$checkout" "$sha" >/dev/null
      if [ ! -f "$checkout/justfile" ]; then
        echo "$sha predates the justfile; gate a lane from the commit that added it" >&2
        exit 1
      fi
      # The server side is already per commit: `uv run` builds the checkout's own .venv
      # from that commit's own uv.lock. web/node_modules is not, and a commit that
      # changed the lockfile would be judged against the lane's dependency graph — the
      # same shape as E8 PR #182, where a commit was gated against something other than
      # its own tree. So it is borrowed only while the two agree on the lockfile, and
      # installed when they do not.
      if cmp -s "$lane_root/web/package-lock.json" "$checkout/web/package-lock.json"; then
        if [ ! -d "$lane_root/web/node_modules" ]; then
          echo "web/node_modules is missing; run 'npm ci' in web/ before gating a lane" >&2
          exit 1
        fi
        ln -s "$lane_root/web/node_modules" "$checkout/web/node_modules"
      else
        echo "    web/package-lock.json differs here; installing this commit's dependencies"
        (cd "$checkout/web" && npm ci)
      fi
      # --working-directory alone is not enough: `just --help` documents it as
      # "use <WORKING_DIRECTORY> as working directory. --justfile must also be set".
      status=0
      just --justfile "$checkout/justfile" --working-directory "$checkout" gate-fast || status=$?
      git -C "$lane_root" worktree remove --force "$checkout"
      if [ "$status" -ne 0 ]; then
        echo "FAILED at $sha ($subject): static checks do not pass on this commit" >&2
        exit 1
      fi
      echo "    ok"
    done
    echo "all $(printf '%s\n' $shas | wc -l) commits in {{base}}..HEAD pass gate-fast"
