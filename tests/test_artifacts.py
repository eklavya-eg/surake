from pathlib import Path
from uuid import uuid4

import pytest

from surake.app.services.artifacts import LocalArtifactStore


def test_artifacts_are_stored_under_project_directory(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    project_id = uuid4()
    artifact_ref = store.save(project_id, "../report?.json", b'{"ok": true}')

    assert artifact_ref.startswith(f"{project_id}/")
    assert store.resolve(artifact_ref).read_bytes() == b'{"ok": true}'


def test_artifact_resolver_blocks_path_traversal(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        store.resolve("../secret.txt")
