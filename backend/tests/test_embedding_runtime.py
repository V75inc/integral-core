"""Hosted inference admission and independent tokenizer regressions."""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from app.services.retrieval import embedding_model
from app.services.retrieval.encoding_runtime import interactive_encoding, run_encoding

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_query_takes_next_slice_before_queued_background():
    started, release = threading.Event(), threading.Event()
    order = []

    def first():
        started.set()
        release.wait(3)
        order.append("first")

    running = asyncio.create_task(run_encoding(first))
    await asyncio.to_thread(started.wait, 2)
    background = asyncio.create_task(run_encoding(lambda: order.append("background")))
    await asyncio.sleep(0)

    async def query():
        with interactive_encoding():
            await run_encoding(lambda: order.append("query"))

    interactive = asyncio.create_task(query())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(running, background, interactive)
    assert order == ["first", "query", "background"]


async def test_cancelled_native_work_retains_admission_until_completion():
    started, release = threading.Event(), threading.Event()
    entered = []

    def blocking():
        started.set()
        release.wait(3)
        entered.append("complete")

    running = asyncio.create_task(run_encoding(blocking))
    await asyncio.to_thread(started.wait, 2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    next_task = asyncio.create_task(run_encoding(lambda: entered.append("next")))
    await asyncio.sleep(0)
    assert entered == []
    release.set()
    await asyncio.wait_for(next_task, 3)
    assert entered == ["complete", "next"]


async def test_interactive_queue_deadline_does_not_start_native_work(monkeypatch):
    from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable

    monkeypatch.setenv("EMBEDDING_QUEUE_TIMEOUT_MS", "10")
    started, release = threading.Event(), threading.Event()

    def blocking():
        started.set()
        release.wait(3)

    running = asyncio.create_task(run_encoding(blocking))
    await asyncio.to_thread(started.wait, 2)
    operation = Mock()
    try:
        with interactive_encoding(), pytest.raises(EmbeddingStoreUnavailable):
            await run_encoding(operation)
        operation.assert_not_called()
    finally:
        release.set()
        await running


async def test_passage_tokenizer_never_loads_inference_weights(monkeypatch):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel

    from app.services.retrieval import onnx_text_encoder

    source = Tokenizer(WordLevel({"[UNK]": 0, "hello": 1}, unk_token="[UNK]"))
    loader = Mock(return_value=source)
    forbidden = Mock(side_effect=AssertionError("Inference weights loaded"))
    monkeypatch.setattr(embedding_model, "_MODEL", None)
    monkeypatch.setattr(embedding_model, "_TOKENIZER", None)
    monkeypatch.setattr(onnx_text_encoder, "load_tokenizer", loader)
    monkeypatch.setattr(embedding_model, "load_model", forbidden)
    first = await embedding_model.passage_tokenizer()
    second = await embedding_model.passage_tokenizer()
    assert first is not second
    assert first.encode("hello").ids == [1]
    loader.assert_called_once()
    forbidden.assert_not_called()


async def test_failed_cold_model_load_is_cooled_down_without_repeated_weight_loads(
    monkeypatch,
):
    from app.services.retrieval import onnx_text_encoder
    from app.services.retrieval.embedding_store import EmbeddingStoreUnavailable

    loader = Mock(side_effect=OSError("private artifact path"))
    monkeypatch.setattr(embedding_model, "_MODEL", None)
    monkeypatch.setattr(embedding_model, "_MODEL_RETRY_AT", 0.0)
    monkeypatch.setattr(onnx_text_encoder, "OnnxTextEncoder", loader)
    for _ in range(2):
        with pytest.raises(EmbeddingStoreUnavailable):
            await run_encoding(embedding_model.load_model)
    loader.assert_called_once()
