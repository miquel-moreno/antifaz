"""The real NER backend: GLiNER (`urchade/gliner_multi_pii-v1`) on CPU, from local files only.

Runs only inside a worker process (ADR-0016). `create` is the factory the pool imports: it sets
the Hugging Face libraries offline, loads the model from the directory checked against the
manifest and never touches the network. The model's `gliner_config.json` names its encoder as
"microsoft/mdeberta-v3-base"; that name is replaced, in memory, by the local copy of the
tokenizer (`mdeberta-v3-base/` in the model directory), so nothing is looked up on the Hub. The
weights are read with `torch.load(weights_only=True)` (no pickled code) and must match the model
exactly (`strict=True`).

**The model's own limit.** Our windows count words (`chunker.py`), but the model reads subword
pieces: a long number or a rare word becomes many pieces, and far past its training length
(384) the model stops finding names without any error (measured: a name after 190 long
numbers, 2,330 pieces, is not seen). So every window is cut again here, with the real
tokenizer, into pieces of at most MAX_MODEL_TOKENS model tokens (labels prompt and special
tokens included) that overlap by OVERLAP_WORDS words. A single word longer than that cannot be
read at all and raises (the request is blocked: fail closed).

The entities of the pieces come back in window offsets. An exact repeat keeps its best score,
but overlapping spans are NOT joined here: the engine joins them after filtering by its
threshold. So the answer at one threshold is the answer at a lower one filtered by score,
which lets the bench score each window once for every candidate threshold (evals/run.py).
"""

import json
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from antifaz.detect.ner.chunker import Chunk, Entity
from antifaz.detect.ner.worker import OFFLINE_ENV

CONFIG_FILE = "gliner_config.json"
WEIGHTS_FILE = "pytorch_model.bin"
TOKENIZER_DIR = "mdeberta-v3-base"
# Model tokens per call, prompt included: the length GLiNER was trained with.
MAX_MODEL_TOKENS = 384
# Words shared by two pieces of one window (an entity shorter than this is whole in one).
OVERLAP_WORDS = 32
BATCH_SIZE = 8
# Model pieces a call may need per text (window) on average. Ordinary text is one piece per
# window; text that costs far more model tokens than words (random CJK, emoji, symbol runs the
# NER view does not hide) is refused before the model runs (TooManyPiecesError): the worker
# answers a fixed error and stays alive, instead of running into the time limit and being
# killed (a reload takes minutes, and every request is blocked meanwhile).
PIECES_PER_TEXT = 4

Words = list[tuple[int, int]]  # (start, end) of each model word in the text


class WordTooLongError(ValueError):
    """One word needs more model tokens than a call can hold: it cannot be read."""


class TooManyPiecesError(ValueError):
    """The texts of a call would need more than PIECES_PER_TEXT model pieces each on average."""


def model_config(model_dir: Path) -> dict[str, Any]:
    """`gliner_config.json` with the encoder pointing to the local tokenizer directory."""
    config: dict[str, Any] = json.loads((model_dir / CONFIG_FILE).read_text(encoding="utf-8"))
    config["model_name"] = str(model_dir / TOKENIZER_DIR)
    return config


def pieces(counts: Sequence[int], budget: int, overlap: int = OVERLAP_WORDS) -> list[range]:
    """Consecutive word ranges covering every word, each with at most `budget` model tokens,
    the next one starting up to `overlap` words before the previous end. The shared words never
    hold more than half the budget in tokens: with a few long words, a full overlap would leave
    each piece only a word or two of new text."""
    if budget < 1:
        raise WordTooLongError("the labels alone fill the model input")
    if any(count > budget for count in counts):
        raise WordTooLongError("a word is longer than the model input")
    ranges: list[range] = []
    first = 0
    while first < len(counts):
        last, used = first, 0
        while last < len(counts) and used + counts[last] <= budget:
            used += counts[last]
            last += 1
        ranges.append(range(first, last))
        if last == len(counts):
            break
        back, shared = last, 0
        while back - 1 > first and last - (back - 1) <= overlap:
            shared += counts[back - 1]
            if shared > budget // 2:
                break
            back -= 1
        first = back
    return ranges


class GlinerBackend:
    """Windows through the model, each cut to fit the model input. Pure logic around three
    functions of the loaded model, so it is tested without torch."""

    def __init__(
        self,
        split_words: Callable[[str], Iterable[tuple[str, int, int]]],
        count_tokens: Callable[[list[str], list[str]], tuple[int, list[int]]],
        infer: Callable[[list[str], list[str], float], list[list[dict[str, Any]]]],
        *,
        max_tokens: int = MAX_MODEL_TOKENS,
        overlap: int = OVERLAP_WORDS,
        pieces_per_text: int = PIECES_PER_TEXT,
    ) -> None:
        self._split_words = split_words
        self._pieces_per_text = pieces_per_text
        self._count_tokens = count_tokens  # (fixed tokens, tokens of each word)
        self._infer = infer
        self._max_tokens = max_tokens
        self._overlap = overlap

    def windows(self, text: str, labels: list[str]) -> list[Chunk]:
        """The pieces of `text` the model reads (none for a text without words)."""
        found = list(self._split_words(text))
        if not found:
            return []
        fixed, counts = self._count_tokens([word for word, _, _ in found], labels)
        if len(counts) != len(found):
            raise ValueError("the tokenizer did not count every word")
        spans: Words = [(start, end) for _, start, end in found]
        chunks = []
        for words in pieces(counts, self._max_tokens - fixed, self._overlap):
            start, end = spans[words[0]][0], spans[words[-1]][1]
            chunks.append(Chunk(start, text[start:end]))
        return chunks

    def predict(
        self, texts: list[str], labels: list[str], threshold: float
    ) -> list[list[list[object]]]:
        windows = [self.windows(text, labels) for text in texts]
        flat = [piece.text for chunks in windows for piece in chunks]
        if len(flat) > self._pieces_per_text * max(1, len(texts)):
            raise TooManyPiecesError("the texts cost too many model tokens for their words")
        raw = iter(self._infer(flat, labels, threshold) if flat else [])
        out: list[list[list[object]]] = []
        for chunks in windows:
            best: dict[tuple[int, int, str], float] = {}
            for piece in chunks:
                for start, end, label, score in map(_entity, next(raw)):
                    key = (piece.start + start, piece.start + end, label)
                    best[key] = max(score, best.get(key, 0.0))
            out.append([[*key, score] for key, score in sorted(best.items())])
        return out


def _entity(item: dict[str, Any]) -> Entity:
    """One GLiNER prediction as (start, end, label, score) with plain Python types."""
    return (int(item["start"]), int(item["end"]), str(item["label"]), float(item["score"]))


def create(model_dir: str, threads: int = 0) -> GlinerBackend:  # pragma: no cover - ner extra
    """Load GLiNER from `model_dir` (already checked against the manifest by the worker).

    Covered by the tests marked `ner_model` (they need the extra and the downloaded model).
    """
    os.environ.update(OFFLINE_ENV)  # before transformers and huggingface_hub are imported
    import torch
    from gliner import GLiNER

    if threads > 0:
        torch.set_num_threads(threads)
    directory = Path(model_dir)
    config = model_config(directory)
    model_class = GLiNER._get_gliner_class(GLiNER._config_from_dict(dict(config)))
    model = model_class.load_from_config(
        config,
        load_tokenizer=True,
        backbone_from_pretrained=False,
        local_files_only=True,
        map_location="cpu",
    )
    state = torch.load(directory / WEIGHTS_FILE, map_location="cpu", weights_only=True)
    model.model.load_state_dict(state, strict=True)
    model.eval()
    processor = model.data_processor
    tokenizer = processor.transformer_tokenizer

    def count_tokens(words: list[str], labels: list[str]) -> tuple[int, list[int]]:
        inputs, prompt_lengths = processor.prepare_inputs([words], labels)
        encoded = tokenizer(inputs, is_split_into_words=True)
        counts = [0] * len(words)
        for word in encoded.word_ids(0):
            if word is not None and word >= prompt_lengths[0]:
                counts[word - prompt_lengths[0]] += 1
        return len(encoded["input_ids"][0]) - sum(counts), counts

    def infer(texts: list[str], labels: list[str], threshold: float) -> list[list[dict[str, Any]]]:
        with torch.inference_mode():
            result: list[list[dict[str, Any]]] = model.inference(
                texts, labels, flat_ner=True, threshold=threshold, batch_size=BATCH_SIZE
            )
        return result

    return GlinerBackend(processor.words_splitter, count_tokens, infer)
