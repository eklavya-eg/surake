import hashlib
import re
from pathlib import Path
from uuid import UUID


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, project_id: UUID, name: str, content: bytes) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
        if not safe_name:
            safe_name = "artifact.bin"
        digest = hashlib.sha256(content).hexdigest()[:16]
        directory = (self.root / str(project_id)).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = (directory / f"{digest}-{safe_name}").resolve()
        if self.root not in target.parents:
            raise ValueError("artifact path escapes the configured root")
        target.write_bytes(content)
        return str(target.relative_to(self.root)).replace("\\", "/")

    def resolve(self, artifact_ref: str) -> Path:
        target = (self.root / artifact_ref).resolve()
        if self.root not in target.parents or not target.is_file():
            raise FileNotFoundError("artifact not found")
        return target

    def delete(self, artifact_ref: str) -> None:
        try:
            self.resolve(artifact_ref).unlink()
        except FileNotFoundError:
            return
