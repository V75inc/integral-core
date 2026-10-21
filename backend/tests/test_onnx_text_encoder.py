"""Text-only encoder contracts, independent of network and downloaded weights."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.retrieval.index_definition import embedding_profile
from app.services.retrieval.onnx_text_encoder import OnnxTextEncoder, encoder_artifact

pytestmark = pytest.mark.smoke


def test_pinned_default_and_explicit_custom_artifacts(monkeypatch):
    artifact = encoder_artifact()
    assert len(artifact.revision) == 40
    with pytest.raises(ValueError, match="repository"):
        encoder_artifact("custom/model")
    monkeypatch.setenv("EMBEDDING_ONNX_REPOSITORY", "example/text-encoder")
    monkeypatch.setenv("EMBEDDING_ONNX_REVISION", "a" * 40)
    monkeypatch.setenv("EMBEDDING_ONNX_POOLING", "cls")
    assert encoder_artifact("custom/model").pooling == "cls"
    monkeypatch.setenv("EMBEDDING_ONNX_REVISION", "main")
    with pytest.raises(ValueError, match="immutable"):
        encoder_artifact("custom/model")


@pytest.mark.parametrize(
    "filename",
    [
        "/model.onnx",
        "../model.onnx",
        "folder/../../model.onnx",
        "folder\\model.onnx",
        "weights.py",
    ],
)
def test_artifact_paths_are_relative_and_data_only(monkeypatch, filename):
    monkeypatch.setenv("EMBEDDING_ONNX_FILE", filename)
    with pytest.raises(ValueError, match="relative ONNX"):
        encoder_artifact()


def test_artifact_and_pooling_changes_get_a_new_vector_space(monkeypatch):
    original = embedding_profile()
    assert replace(original, encoder_artifact="different").id != original.id
    monkeypatch.setenv("EMBEDDING_ONNX_POOLING", "cls")
    assert embedding_profile().id != original.id


def fake_encoder(output, pooling="mean"):
    encoder = object.__new__(OnnxTextEncoder)
    item = SimpleNamespace(ids=[1, 2], attention_mask=[1, 0])
    encoder.tokenizer = SimpleNamespace(encode_batch=lambda texts: [item] * len(texts))
    encoder.inputs = {"input_ids", "attention_mask", "token_type_ids"}
    encoder.pooling = pooling

    def run(names, inputs):
        assert all(value.dtype == np.int64 for value in inputs.values())
        return [np.asarray(output, dtype=np.float32)]

    encoder.session = SimpleNamespace(run=run)
    return encoder


@pytest.mark.parametrize("pooling", ["mean", "cls"])
def test_padding_mask_and_normalization(pooling):
    encoder = fake_encoder([[[3, 4], [100, 100]]], pooling)
    np.testing.assert_allclose(encoder.encode("text"), [0.6, 0.8], atol=1e-7)


def test_pooled_output_and_rejected_invalid_vectors():
    encoder = fake_encoder([[3, 4]], "pooled")
    np.testing.assert_allclose(encoder.encode("text"), [0.6, 0.8], atol=1e-7)
    for output in ([[float("nan"), 1]], [[0, 0]]):
        with pytest.raises(ValueError, match="invalid vector"):
            fake_encoder(output, "pooled").encode("text")
    with pytest.raises(ValueError, match="one vector per input token"):
        fake_encoder([[3, 4]], "mean").encode("text")
    with pytest.raises(ValueError, match="between 1 and 16"):
        list(encoder.embed(["text"], batch_size=17))


def test_additional_weights_are_explicit_and_profile_bound(monkeypatch):
    original = encoder_artifact()
    monkeypatch.setenv("EMBEDDING_ONNX_ADDITIONAL_FILES", '["model.onnx_data"]')
    assert encoder_artifact().fingerprint != original.fingerprint
    monkeypatch.setenv("EMBEDDING_ONNX_ADDITIONAL_FILES", '["../weights"]')
    with pytest.raises(ValueError, match="relative artifact"):
        encoder_artifact()
