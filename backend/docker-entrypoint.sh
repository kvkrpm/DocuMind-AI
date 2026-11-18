#!/bin/sh
set -eu

mkdir -p "$UPLOAD_DIR" "$CHROMA_PERSIST_DIR"
chown app:app "$UPLOAD_DIR" "$CHROMA_PERSIST_DIR"
exec gosu app "$@"
