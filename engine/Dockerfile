# Rust toolchain for the PrivGuard engine.
#
# Pinned to a specific minor release so a build here and a build in CI agree.
FROM rust:1.83-slim

WORKDIR /engine

ENV CARGO_TERM_COLOR=always \
    RUST_BACKTRACE=1

# The -slim variant ships neither rustfmt nor clippy, and CI gates on both, so
# install them here: whatever CI checks must be checkable locally.
RUN rustup component add rustfmt clippy

# The foundation phase has no external crate dependencies, so this build needs
# no network access at all. Dependencies arrive per-phase; when they do, split
# this into a manifest-only layer followed by the source copy so the dependency
# build caches independently.
COPY . .

RUN cargo build --workspace

# Default to the test suite: `docker compose run --rm engine` should tell you
# whether the engine is healthy, not silently exit.
CMD ["cargo", "test", "--workspace"]
