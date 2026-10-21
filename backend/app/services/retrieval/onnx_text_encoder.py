"""Pinned, text-only ONNX inference without an image-library dependency.

Heavy imports stay inside initialization/inference. Deployment settings select
trusted artifacts; chat, index declarations and model output cannot change them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Iterator

_DEFAULT_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_REPO = "qdrant/all-MiniLM-L6-v2-onnx"
_DEFAULT_REVISION = "d13954661f83248295ba75c1ed411eef3b7b936e"


@dataclass(frozen=True)
class EncoderArtifact:
    """Immutable artifact and preprocessing identity for an embedding space."""

    repository: str
    revision: str
    model_file: str
    pooling: str
    additional_files: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True).encode()
        ).hexdigest()


def encoder_artifact(model_name: str = _DEFAULT_NAME) -> EncoderArtifact:
    """Resolve explicit, pinned deployment configuration; never guess pooling."""
    custom = model_name != _DEFAULT_NAME
    repo = os.getenv("EMBEDDING_ONNX_REPOSITORY", "" if custom else _DEFAULT_REPO)
    revision = os.getenv("EMBEDDING_ONNX_REVISION", "" if custom else _DEFAULT_REVISION)
    pooling = os.getenv("EMBEDDING_ONNX_POOLING", "" if custom else "mean")
    filename = os.getenv("EMBEDDING_ONNX_FILE", "model.onnx")
    if not repo or not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError(
            "The search model needs a repository and immutable commit revision"
        )
    if pooling not in {"mean", "cls", "pooled"}:
        raise ValueError(
            "The search model needs explicit mean, cls or pooled processing"
        )
    path = PurePosixPath(filename)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in filename
        or not filename.endswith(".onnx")
    ):
        raise ValueError("The search model file must be a relative ONNX path")
    files = json.loads(os.getenv("EMBEDDING_ONNX_ADDITIONAL_FILES", "[]"))
    if not isinstance(files, list) or len(files) > 32:
        raise ValueError(
            "The search model supports at most 32 additional artifact files"
        )
    for extra in files:
        if not isinstance(extra, str) or not extra or "\\" in extra:
            raise ValueError("Additional model files must be relative artifact paths")
        extra_path = PurePosixPath(extra)
        if extra_path.is_absolute() or ".." in extra_path.parts:
            raise ValueError("Additional model files must be relative artifact paths")
    return EncoderArtifact(repo, revision.lower(), filename, pooling, tuple(files))


class OnnxTextEncoder:
    """Bounded CPU encoder with the existing encode/embedding batch surface."""

    def __init__(self, artifact: EncoderArtifact, threads: int) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import AddedToken, Tokenizer

        cache = os.getenv("EMBEDDING_MODEL_CACHE_PATH") or os.getenv(
            "FASTEMBED_CACHE_PATH"
        )

        def download(filename: str) -> str:
            return str(
                hf_hub_download(
                    artifact.repository,
                    filename,
                    revision=artifact.revision,
                    cache_dir=cache,
                    local_files_only=os.getenv("HF_HUB_OFFLINE", "").lower()
                    in {"1", "true"},
                )
            )

        from pathlib import Path

        tokenizer_config = json.loads(
            Path(download("tokenizer_config.json")).read_text()
        )
        config = json.loads(Path(download("config.json")).read_text())
        special = json.loads(Path(download("special_tokens_map.json")).read_text())
        tokenizer = Tokenizer.from_file(download("tokenizer.json"))
        limits = [
            tokenizer_config[k]
            for k in ("model_max_length", "max_length")
            if k in tokenizer_config
        ]
        if not limits or not 2 <= int(min(limits)) <= 32768:
            raise ValueError("The search tokenizer needs a bounded model token limit")
        tokenizer.enable_truncation(max_length=int(min(limits)))
        if not tokenizer.padding:
            tokenizer.enable_padding(
                pad_id=int(config.get("pad_token_id", 0)),
                pad_token=tokenizer_config["pad_token"],
            )
        for token in special.values():
            tokenizer.add_special_tokens(
                [AddedToken(**token) if isinstance(token, dict) else token]
            )
        self.tokenizer = tokenizer
        self.pooling = artifact.pooling
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(16, max(1, threads))
        options.inter_op_num_threads = 1
        for extra in artifact.additional_files:
            download(extra)
        self.session = ort.InferenceSession(
            download(artifact.model_file),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.inputs = {node.name for node in self.session.get_inputs()}
        if (
            not self.inputs <= {"input_ids", "attention_mask", "token_type_ids"}
            or "input_ids" not in self.inputs
        ):
            raise ValueError("The ONNX model does not expose supported text inputs")

    def embed(self, texts: list[str], batch_size: int = 16) -> Iterator[Any]:
        """Encode bounded batches; preserve the selected model's pooling contract."""
        import numpy as np

        if not 1 <= batch_size <= 16:
            raise ValueError("Embedding batches must contain between 1 and 16 texts")
        for start in range(0, len(texts), batch_size):
            encoded = self.tokenizer.encode_batch(texts[start : start + batch_size])
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            values = {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            }
            output = self.session.run(None, {key: values[key] for key in self.inputs})[
                0
            ]
            if self.pooling == "pooled":
                if output.ndim != 2 or output.shape[0] != len(encoded):
                    raise ValueError("The model must return pooled sentence vectors")
                vectors = output
            else:
                if output.ndim != 3 or output.shape[:2] != mask.shape:
                    raise ValueError("The model must return one vector per input token")
                if self.pooling == "cls":
                    vectors = output[:, 0]
                else:
                    weights = mask[..., None].astype(output.dtype)
                    vectors = (output * weights).sum(axis=1) / np.maximum(
                        weights.sum(axis=1), 1e-9
                    )
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (
                not np.isfinite(vectors).all()
                or not np.isfinite(norms).all()
                or (norms <= 0).any()
            ):
                raise ValueError("The search model returned an invalid vector")
            yield from vectors / np.maximum(norms, 1e-12)

    def encode(self, text: str, convert_to_numpy: bool = False) -> Any:
        """Keep the public singleton surface compatible with existing callers."""
        vector = next(self.embed([text]))
        return vector if convert_to_numpy else [float(value) for value in vector]
