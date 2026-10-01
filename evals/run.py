"""Run Antifaz-Bench on MEDDOCAN and write the report (labels, counts and metrics only).

With --ner (`make bench NER=1`) it also runs the NER model (`make ner-model` first): the
threshold is chosen on the MEDDOCAN dev split, then the test split is measured ONCE with it,
with the cache cold and warm. The report records the model revision and the manifest hash.

With --presidio (`make bench PRESIDIO=1`, the `bench` dependency group) it also measures
Presidio on the MEDDOCAN test split with the same metrics, and compares it with Antifaz only on
the types both cover (evals/presidio_baseline.py). The NER numbers come from the latest
committed results; the NER is not run again.
"""

import argparse
import csv
import io
import json
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from antifaz import __version__
from antifaz.config import Settings
from antifaz.detect.ner.backend import Predictor
from antifaz.detect.ner.cache import SpanCache
from antifaz.detect.ner.engine import NerDetector
from antifaz.detect.ner.manifest import (
    DEFAULT_MANIFEST,
    Manifest,
    load_manifest,
    verify_model_dir,
)
from antifaz.detect.ner.pool import NerPool
from antifaz.detect.ner.setup import GLINER_FACTORY
from antifaz.detect.scan import Scanner, scan
from antifaz.detect.types import EntityType, Span
from antifaz.errors import DetectorFailed
from evals import presidio_baseline
from evals.datasets import meddocan
from evals.datasets.meddocan import Document
from evals.generate import from_jsonl
from evals.metrics import (
    Annotation,
    Counts,
    any_gold_counts,
    latency_ms,
    leaks,
    leaks_per_100,
    overlap_counts,
    strict_counts,
)

NER_START = "<!-- bench-ner:start -->"
NER_END = "<!-- bench-ner:end -->"
# Candidate NER thresholds, and the least precision PERSON and ADDRESS must keep on the dev
# split. The 85 % was fixed before looking at any result: the floor stops a low threshold
# from "winning" by masking half the text. WHAT it is measured against changed after the first
# dev run (ADR-0011, amendment of 2026-10-01): against any annotated personal data.
THRESHOLDS = (0.3, 0.4, 0.5, 0.6)
PRECISION_FLOOR = 0.85
SELECTION_RULE = (
    "Among the candidate thresholds on MEDDOCAN dev, the one with the fewest leaks per 100 "
    "(covered types) whose precision against any annotated personal data (a prediction is "
    "correct if it overlaps a gold span of ANY MEDDOCAN label) is at least 85 % for both "
    "PERSON and ADDRESS; a tie goes to the higher threshold. By-type (overlap) and strict "
    "precision are published too. Fallback, floor not met: the most precise threshold (highest "
    "precision against any personal data of the weakest floor type; a tie goes to the higher "
    "threshold), test is measured once with it and the result says floor_met: false (NER not "
    "recommended by default)."
)
FLOOR_NOT_MET = (
    "El NER no cumple el suelo de precisión del 85 % en nombres; se publica como opcional y "
    "desactivado por defecto."
)
SELECTION_RULE_CHANGED = (
    "floor measured against any annotated personal data, decided 2026-10-01 after the first "
    "dev run showed by-type precision 63\u201369 % (PERSON) / 56\u201358 % (ADDRESS)"
)
START_MARKER = "<!-- bench:start -->"
END_MARKER = "<!-- bench:end -->"
SYNTHETIC_START = "<!-- bench-synthetic:start -->"
SYNTHETIC_END = "<!-- bench-synthetic:end -->"
PRESIDIO_START = "<!-- bench-presidio:start -->"
PRESIDIO_END = "<!-- bench-presidio:end -->"
ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "evals" / "results"
BENCHMARK_DOC = ROOT / "docs" / "benchmark.md"
SYNTHETIC = ROOT / "evals" / "datasets" / "synthetic-v1.jsonl"


@dataclass
class Report:
    """Benchmark result. It never holds any text or value from the documents.

    by_source_label: MEDDOCAN label -> {"type": Antifaz type or None, "n", "precision",
        "recall", "f1" (None for unmapped labels), "leaks", "leaks_per_100"}.
    by_type: Antifaz type -> {"tp", "fp", "fn", "precision", "recall", "f1" (overlap),
        "strict_precision", "strict_recall", "strict_f1", "any_pii_precision" (against gold
        of ANY label, metrics.any_gold_counts), "non_pii_fp" (predictions that mask text
        that is not personal data at all)}.
    overall: {"total", "leaked", "leaks_per_100", "covered_total", "covered_leaked",
        "covered_leaks_per_100"}; covered_* only counts labels mapped to a type.
    latency_ms: {"p50", "p95"} of detect() per document.
    """

    dataset: str
    documents: int
    by_source_label: dict[str, dict[str, float | int | str | None]]
    by_type: dict[str, dict[str, float | int]]
    overall: dict[str, float | int]
    latency_ms: dict[str, float]
    environment: dict[str, str]
    # Antifaz types with no dataset label mapped to them -> {"detections",
    # "overlapping_gold"}: shown so that false positives of those types are not hidden.
    unmatched_types: dict[str, dict[str, int]] = field(default_factory=dict)
    # Only with the NER: threshold and how it was chosen, warm-cache latency, model revision,
    # manifest hash and memory of the worker process.
    ner: dict[str, object] = field(default_factory=dict)
    # Only for another tool measured the same way (Presidio): its name, versions, configuration
    # and the types compared.
    baseline: dict[str, object] = field(default_factory=dict)


def _add(a: Counts, b: Counts) -> Counts:
    return Counts(a.tp + b.tp, a.fp + b.fp, a.fn + b.fn)


def _mapped(
    annotations: Sequence[Annotation], mapping: Mapping[str, EntityType | None]
) -> list[Annotation]:
    """Gold annotations relabelled with their Antifaz type (unmapped ones are dropped)."""
    result = []
    for annotation in annotations:
        entity_type = mapping.get(annotation.label)
        if entity_type is not None:
            result.append(Annotation(annotation.start, annotation.end, entity_type.value))
    return result


def _environment() -> dict[str, str]:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed git subcommands, no user input
            ["git", *args],  # noqa: S607 - git from PATH, fixed args
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "--short", "HEAD")
        # Results computed with uncommitted changes cannot be reproduced from the commit.
        if git("status", "--porcelain", "--untracked-files=no"):
            commit += "-dirty"
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {
        "date": date.today().isoformat(),
        "antifaz": __version__,
        "commit": commit,
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
    }


def evaluate(
    documents: Sequence[Document],
    mapping: Mapping[str, EntityType | None],
    detect: Callable[[str], Sequence[Span | Annotation]] = scan,
    *,
    dataset: str = "custom documents",
) -> Report:
    """Run detect on every document and compare it with the gold annotations. detect returns
    Antifaz Spans, or Annotations labelled with an Antifaz type or, when the tool has a type
    with no Antifaz equivalent, with that tool's own name (Presidio's LOCATION)."""
    overlap: dict[str, Counts] = defaultdict(Counts)
    strict: dict[str, Counts] = defaultdict(Counts)
    any_gold: dict[str, Counts] = defaultdict(Counts)
    source_recall: dict[str, Counts] = defaultdict(Counts)
    source_leaks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    samples: list[int] = []
    unmatched: dict[str, dict[str, int]] = defaultdict(
        lambda: {"detections": 0, "overlapping_gold": 0}
    )
    mapped_types = {t.value for t in mapping.values() if t is not None}

    unknown = sorted({a.label for d in documents for a in d.annotations} - set(mapping))
    if unknown:
        raise ValueError(f"dataset labels missing from the mapping: {', '.join(unknown)}")
    if documents:
        detect(documents[0].text)  # warm-up: compiled patterns and caches, not timed

    for document in documents:
        started = time.perf_counter_ns()
        spans = detect(document.text)
        samples.append(time.perf_counter_ns() - started)
        predicted = [
            s if isinstance(s, Annotation) else Annotation(s.start, s.end, s.type.value)
            for s in spans
        ]
        gold = _mapped(document.annotations, mapping)
        for p in predicted:
            if p.label not in mapped_types:
                unmatched[p.label]["detections"] += 1
                if any(a.start < p.end and p.start < a.end for a in document.annotations):
                    unmatched[p.label]["overlapping_gold"] += 1
        for label, counts in overlap_counts(gold, predicted).items():
            overlap[label] = _add(overlap[label], counts)
        for label, counts in strict_counts(gold, predicted).items():
            strict[label] = _add(strict[label], counts)
        # Against every annotation, mapped or not: over-masking other personal data is fine.
        for label, counts in any_gold_counts(document.annotations, predicted).items():
            any_gold[label] = _add(any_gold[label], counts)
        for label, (leaked, total) in leaks(document.text, document.annotations, predicted).items():
            source_leaks[label][0] += leaked
            source_leaks[label][1] += total
        # Recall per source label: its gold values against predictions of its mapped type.
        for label in {a.label for a in document.annotations}:
            entity_type = mapping.get(label)
            if entity_type is None:
                continue
            own = [a for a in document.annotations if a.label == label]
            found = overlap_counts(_mapped(own, mapping), predicted).get(entity_type.value)
            if found is not None:
                source_recall[label] = _add(source_recall[label], Counts(found.tp, 0, found.fn))

    by_source_label: dict[str, dict[str, float | int | str | None]] = {}
    for label, (leaked, total) in sorted(source_leaks.items()):
        entity_type = mapping.get(label)
        by_source_label[label] = {
            "type": entity_type.value if entity_type else None,
            "n": total,
            # Precision is not defined per source label (predictions have Antifaz types).
            "precision": None,
            "recall": source_recall[label].recall if entity_type else None,
            "f1": None,
            "leaks": leaked,
            "leaks_per_100": leaks_per_100(leaked, total),
        }

    by_type: dict[str, dict[str, float | int]] = {}
    for label in sorted(set(overlap) & mapped_types):
        o, s, a = overlap[label], strict[label], any_gold[label]
        by_type[label] = {
            "tp": o.tp,
            "fp": o.fp,
            "fn": o.fn,
            "precision": o.precision,
            "recall": o.recall,
            "f1": o.f1,
            "strict_precision": s.precision,
            "strict_recall": s.recall,
            "strict_f1": s.f1,
            "any_pii_precision": a.precision,
            "non_pii_fp": a.fp,
        }

    total = sum(n for _, n in source_leaks.values())
    leaked = sum(n for n, _ in source_leaks.values())
    covered = [v for k, v in source_leaks.items() if mapping.get(k) is not None]
    covered_total = sum(n for _, n in covered)
    covered_leaked = sum(n for n, _ in covered)
    p50, p95 = latency_ms(samples) if samples else (0.0, 0.0)
    return Report(
        dataset=dataset,
        documents=len(documents),
        by_source_label=by_source_label,
        by_type=by_type,
        overall={
            "total": total,
            "leaked": leaked,
            "leaks_per_100": leaks_per_100(leaked, total),
            "covered_total": covered_total,
            "covered_leaked": covered_leaked,
            "covered_leaks_per_100": leaks_per_100(covered_leaked, covered_total),
        },
        latency_ms={"p50": p50, "p95": p95},
        environment=_environment(),
        unmatched_types={k: dict(v) for k, v in sorted(unmatched.items())},
    )


def to_json(report: Report) -> str:
    """The report as JSON (the fields of Report at the top level)."""
    return json.dumps(asdict(report), ensure_ascii=False, indent=1) + "\n"


def _pct(value: float | int | str | None) -> str:
    return "—" if value is None or isinstance(value, str) else f"{100 * value:.1f} %"


def render_markdown(report: Report) -> str:
    """Markdown tables under the headings "## Tipos cubiertos", "## Tipos aún no
    cubiertos" and "## Global"."""
    covered = {k: v for k, v in report.by_source_label.items() if v["type"] is not None}
    missing = {k: v for k, v in report.by_source_label.items() if v["type"] is None}
    lines = [
        "## Tipos cubiertos",
        "",
        "| Tipo en el dataset | Tipo Antifaz | Datos | Recall (solape) | Fugas por cada 100 |",
        "|---|---|---|---|---|",
    ]
    for label, row in covered.items():
        lines.append(
            f"| {label} | {row['type']} | {row['n']} | {_pct(row['recall'])} "
            f"| {float(row['leaks_per_100'] or 0):.1f} |"
        )
    lines += [
        "",
        "| Tipo Antifaz | Precisión (mismo tipo) | Precisión estricta "
        "| Precisión (cualquier dato personal) | Tapan texto no personal | Recall | F1 "
        "| F1 estricto |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, metrics in report.by_type.items():
        lines.append(
            f"| {label} | {_pct(metrics['precision'])} | {_pct(metrics['strict_precision'])} "
            f"| {_pct(metrics.get('any_pii_precision'))} | {metrics.get('non_pii_fp', '—')} "
            f"| {_pct(metrics['recall'])} | {_pct(metrics['f1'])} | {_pct(metrics['strict_f1'])} |"
        )
    lines += [
        "",
        "Precisión del mismo tipo: la detección se solapa con un dato anotado de su tipo. "
        "Contra cualquier dato personal: se solapa con un dato anotado de cualquier tipo "
        "(también los que Antifaz no cubre); solo es un error si tapa texto que no es un dato "
        "personal.",
    ]
    lines += [
        "",
        "## Tipos aún no cubiertos",
        "",
        "Antifaz no busca estos datos con esta configuración (el NER existe, es opcional y",
        "viene apagado). Se miden igual: sus fugas cuentan en la cifra global.",
        "",
        "| Tipo en el dataset | Datos | Fugas por cada 100 |",
        "|---|---|---|",
    ]
    for label, row in missing.items():
        lines.append(f"| {label} | {row['n']} | {float(row['leaks_per_100'] or 0):.1f} |")
    lines += [
        "",
        "Una detección de cualquier tipo tapa el dato: por eso algunos tipos no cubiertos no",
        "llegan a 100 fugas (por ejemplo, fechas tapadas por DATE_OF_BIRTH).",
        "",
        "## Detecciones de tipos sin equivalente en el dataset",
        "",
        "Tipos que Antifaz detecta pero que el dataset no anota con una etiqueta propia. Si no",
        "tocan ningún dato anotado, son falsos positivos.",
        "",
        "| Tipo Antifaz | Detecciones | Tocan un dato anotado |",
        "|---|---|---|",
    ]
    for label, counts in report.unmatched_types.items():
        lines.append(f"| {label} | {counts['detections']} | {counts['overlapping_gold']} |")
    if not report.unmatched_types:
        lines.append("| — | 0 | 0 |")
    overall = report.overall
    lines += [
        "",
        "## Global",
        "",
        "| Medida | Valor |",
        "|---|---|",
        f"| Documentos | {report.documents} |",
        f"| Datos personales anotados | {overall['total']} |",
        f"| Fugas por cada 100 (todos los tipos) | {float(overall['leaks_per_100']):.1f} |",
        f"| Fugas por cada 100 (tipos cubiertos) | {float(overall['covered_leaks_per_100']):.1f} |",
        f"| Latencia p50 / p95 por documento | {report.latency_ms['p50']:.2f} ms / "
        f"{report.latency_ms['p95']:.2f} ms |",
    ]
    return "\n".join(lines) + "\n"


def update_benchmark_doc(
    doc: Path, table: str, start_marker: str = START_MARKER, end_marker: str = END_MARKER
) -> None:
    """Replace what is between the markers with table.strip(), keeping the markers on
    their own lines. Raises ValueError (file untouched) if a marker is missing."""
    text = doc.read_text(encoding="utf-8")
    start, end = text.find(start_marker), text.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{doc.name}: benchmark markers not found")
    new = text[: start + len(start_marker)] + "\n" + table.strip() + "\n" + text[end:]
    doc.write_text(new, encoding="utf-8", newline="\n")


def _save(report: Report, suffix: str) -> str:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{report.environment['date']}-{report.environment['antifaz']}{suffix}.json"
    (RESULTS_DIR / name).write_text(to_json(report), encoding="utf-8", newline="\n")
    return name


def run_synthetic(documents: Sequence[Document]) -> Report:
    """The synthetic set is labelled directly with Antifaz types."""
    return evaluate(documents, SYNTHETIC_MAPPING, dataset=SYNTHETIC_DATASET)


@dataclass
class NerBench:
    report: Report  # test split, chosen threshold, cache cold
    threshold: float
    selection: list[dict[str, object]]  # one row per candidate threshold, dev split
    warm_latency_ms: dict[str, float]  # the same test documents again: every text cached
    floor_met: bool = True  # False: no threshold kept the floor, `threshold` is the fallback


class NoThresholdError(ValueError):
    """No candidate threshold keeps the precision floor (against any personal data) on dev."""


def _well_formed(entity: object) -> bool:
    return isinstance(entity, list | tuple) and len(entity) == 4 and type(entity[3]) in (int, float)


class ScoreCache:
    """A predictor in front of another: every window is scored ONCE, at the lowest threshold,
    and the answer is filtered by score for each higher threshold.

    Exact, not an approximation: GLiNER's greedy decoding keeps, above any threshold, the same
    spans it keeps at a lower one filtered by score (higher scores are chosen first), and the
    backend never joins spans (gliner.py; the engine joins them after its own filter). A test
    checks it gives the same spans as running every threshold. Only for the bench: it keeps
    the window texts in memory."""

    def __init__(self, inner: Predictor, threshold: float) -> None:
        self._inner = inner
        self._threshold = threshold
        self._scores: dict[tuple[str, tuple[str, ...]], list[object]] = {}

    @property
    def timeout(self) -> float | None:
        timeout = getattr(self._inner, "timeout", None)
        return float(timeout) if timeout is not None else None

    def start(self) -> None:
        self._inner.start()

    def close(self) -> None:
        self._inner.close()

    def predict(
        self,
        texts: Sequence[str],
        labels: Sequence[str],
        threshold: float,
        deadline: float | None = None,
    ) -> list[list[object]]:
        if threshold < self._threshold:
            raise ValueError("a threshold below the one the windows were scored with")
        names = tuple(labels)
        missing = [text for text in dict.fromkeys(texts) if (text, names) not in self._scores]
        if missing:
            if deadline is not None and self.timeout is not None:
                raw = self._inner.predict(missing, names, self._threshold, deadline=deadline)
            else:
                raw = self._inner.predict(missing, names, self._threshold)
            if not isinstance(raw, list | tuple) or len(raw) != len(missing):
                raise DetectorFailed()
            for text, answer in zip(missing, raw, strict=True):
                self._scores[(text, names)] = list(answer) if isinstance(answer, list) else answer
        out = []
        for text in texts:
            answer = self._scores[(text, names)]
            if not isinstance(answer, list):
                out.append(answer)  # malformed: the engine refuses it
                continue
            # GLiNER keeps scores strictly above its threshold (probs > threshold): so does this.
            # Malformed entities are kept so the engine refuses them, as without the cache.
            out.append([e for e in answer if not _well_formed(e) or float(e[3]) > threshold])
        return out


def selection_row(
    threshold: float, report: Report, floor: float = PRECISION_FLOOR
) -> dict[str, object]:
    """One row of the dev table: numbers only (no text, no value)."""
    row: dict[str, object] = {
        "threshold": threshold,
        "documents": report.documents,
        "leaks_per_100": float(report.overall["leaks_per_100"]),
        "covered_leaks_per_100": float(report.overall["covered_leaks_per_100"]),
    }
    for entity in ("PERSON", "ADDRESS"):
        metrics = report.by_type.get(entity, {})
        tp, fp, fn = (int(metrics.get(name, 0)) for name in ("tp", "fp", "fn"))
        precision = float(metrics.get("precision", 0.0))
        any_pii = float(metrics.get("any_pii_precision", 0.0))
        row |= {
            f"{entity}_precision": precision,
            f"{entity}_recall": float(metrics.get("recall", 0.0)),
            f"{entity}_strict_precision": float(metrics.get("strict_precision", 0.0)),
            f"{entity}_strict_recall": float(metrics.get("strict_recall", 0.0)),
            f"{entity}_tp": tp,
            f"{entity}_fp": fp,
            f"{entity}_fn": fn,
            f"{entity}_gold": tp + fn,
            f"{entity}_any_pii_precision": any_pii,
            f"{entity}_non_pii_fp": int(metrics.get("non_pii_fp", 0)),
            # The floor is measured against any personal data (ADR-0011, 2026-10-01); the
            # by-type result is kept next to it.
            f"{entity}_meets_floor": any_pii >= floor,
            f"{entity}_meets_floor_by_type": precision >= floor,
        }
    return row


FLOOR_TYPES = ("PERSON", "ADDRESS")


def _number(row: Mapping[str, object], key: str) -> float:
    value = row[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} is not a number")
    return float(value)


def choose_threshold(rows: Sequence[Mapping[str, object]], floor: float = PRECISION_FLOOR) -> float:
    """Fewest covered leaks per 100 among the thresholds whose PERSON and ADDRESS precision
    against any annotated personal data both reach `floor` (SELECTION_RULE); a tie goes to the
    higher threshold (fewer false positives). NoThresholdError says, for each threshold, which
    type falls short."""
    key = "any_pii_precision"
    good = [row for row in rows if all(_number(row, f"{e}_{key}") >= floor for e in FLOOR_TYPES)]
    if not good:
        failing = "; ".join(
            f"{row['threshold']}: "
            + ", ".join(
                f"{entity} {100 * _number(row, f'{entity}_{key}'):.1f} %"
                for entity in FLOOR_TYPES
                if _number(row, f"{entity}_{key}") < floor
            )
            for row in rows
        )
        raise NoThresholdError(
            f"no threshold keeps the precision floor of {100 * floor:.0f} % against any "
            f"personal data ({failing})"
        )
    best = min(good, key=lambda r: (_number(r, "covered_leaks_per_100"), -_number(r, "threshold")))
    return _number(best, "threshold")


def select_threshold(
    rows: Sequence[Mapping[str, object]], floor: float = PRECISION_FLOOR
) -> tuple[float, bool]:
    """(threshold, floor_met). With the floor met, choose_threshold. If not, the fallback of
    SELECTION_RULE: the threshold whose weakest floor type (PERSON on MEDDOCAN dev) has the
    highest precision against any personal data; a tie goes to the higher threshold."""
    try:
        return choose_threshold(rows, floor), True
    except NoThresholdError:
        pass
    if not rows:
        raise NoThresholdError("no candidate thresholds")
    best = max(
        rows,
        key=lambda r: (
            min(_number(r, f"{e}_any_pii_precision") for e in FLOOR_TYPES),
            _number(r, "threshold"),
        ),
    )
    return _number(best, "threshold"), False


def dev_selection(
    dev: Sequence[Document],
    predictor: Predictor,
    thresholds: Sequence[float] = THRESHOLDS,
    *,
    model_id: str = "unknown",
    floor: float = PRECISION_FLOOR,
) -> list[dict[str, object]]:
    """The dev table: every threshold, the model run once per window (ScoreCache)."""
    mapping = meddocan.MEDDOCAN_TO_ANTIFAZ_NER
    scored = ScoreCache(predictor, min(thresholds))
    rows = []
    for threshold in thresholds:
        detect = Scanner(NerDetector(scored, threshold=threshold, model_id=model_id))
        rows.append(selection_row(threshold, evaluate(dev, mapping, detect), floor))
    return rows


def measure_test(
    test: Sequence[Document], predictor: Predictor, threshold: float, model_id: str = "unknown"
) -> tuple[Report, dict[str, float]]:
    """The test split once with `threshold` (cache cold), then again (every text cached)."""
    mapping = meddocan.MEDDOCAN_TO_ANTIFAZ_NER
    detector = NerDetector(
        predictor, threshold=threshold, cache=SpanCache(10_000), model_id=model_id
    )
    scanner = Scanner(detector)
    report = evaluate(
        test, mapping, scanner, dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)"
    )
    warm = evaluate(test, mapping, scanner)
    return report, warm.latency_ms


def run_ner_bench(
    dev: Sequence[Document],
    test: Sequence[Document],
    predictor: Predictor,
    thresholds: Sequence[float] = THRESHOLDS,
    floor: float = PRECISION_FLOOR,
    model_id: str = "unknown",
) -> NerBench:
    """Choose the threshold on `dev`, then measure `test` once with it (cold, then warm). With
    the floor not met, test is still measured once with the fallback threshold."""
    rows = dev_selection(dev, predictor, thresholds, model_id=model_id, floor=floor)
    chosen, floor_met = select_threshold(rows, floor)
    report, warm = measure_test(test, predictor, chosen, model_id)
    return NerBench(report, chosen, rows, warm, floor_met)


def rss_mb(pid: int) -> float | None:
    """Resident memory of a process in MB with OS tools (no psutil): /proc on Linux, tasklist
    on Windows. None where neither works."""
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
        return None
    if sys.platform != "win32":
        return None
    listing = subprocess.run(  # noqa: S603 - fixed program, the pid is an int
        ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for row in csv.reader(io.StringIO(listing)):
        if len(row) >= 5 and row[1] == str(pid):
            return int("".join(ch for ch in row[4] if ch.isdigit())) / 1024  # "1.234.567 K"
    return None


def _pct_value(value: object) -> str:
    return f"{100 * value:.1f} %" if isinstance(value, int | float) else "—"


def _leaks(report: Report, labels: Sequence[str]) -> str:
    rows = [report.by_source_label[label] for label in labels if label in report.by_source_label]
    total = sum(int(row["n"] or 0) for row in rows)
    leaked = sum(int(row["leaks"] or 0) for row in rows)
    return f"{leaks_per_100(leaked, total):.1f}"


def render_selection(rows: Sequence[Mapping[str, object]]) -> str:
    """The dev table as Markdown (also printed before choosing). Per type: precision against
    any personal data (the floor), of the same type (overlap), strict, recall and how many
    detections mask text that is not personal data."""
    header = "| Umbral | Fugas por cada 100 (todos) | Fugas (cubiertos) |"
    for entity in FLOOR_TYPES:
        header += (
            f" {entity}: precisión (cualquier dato personal) | {entity}: precisión (mismo "
            f"tipo) | {entity}: precisión estricta | {entity}: recall | {entity}: tapan texto "
            "no personal |"
        )
    header += " Suelo PERSON / ADDRESS |"
    lines = [header, "|" + "---|" * (4 + 5 * len(FLOOR_TYPES))]
    for row in rows:
        line = (
            f"| {row['threshold']} | {_number(row, 'leaks_per_100'):.1f} | "
            f"{_number(row, 'covered_leaks_per_100'):.1f} |"
        )
        for e in FLOOR_TYPES:
            line += (
                f" {_pct_value(row[f'{e}_any_pii_precision'])} | "
                f"{_pct_value(row[f'{e}_precision'])} | "
                f"{_pct_value(row[f'{e}_strict_precision'])} | "
                f"{_pct_value(row[f'{e}_recall'])} | {row[f'{e}_non_pii_fp']} |"
            )
        floor = " / ".join("sí" if row[f"{e}_meets_floor"] else "no" for e in FLOOR_TYPES)
        lines.append(f"{line} {floor} |")
    return "\n".join(lines) + "\n"


def render_ner_markdown(report: Report, base: Report) -> str:
    """The NER section: how the threshold was chosen (dev), before/after on test, and the
    usual tables with the NER on. Numbers only."""
    ner = report.ner
    selection = ner.get("selection")
    selection = selection if isinstance(selection, list) else []
    warm = ner.get("latency_warm_ms")
    warm = warm if isinstance(warm, dict) else {}
    lines = [
        "## Con NER: umbral elegido en dev",
        "",
        f"Umbrales probados en la partición **dev** de MEDDOCAN. Se elige el de menos fugas "
        f"(tipos cubiertos) con precisión de PERSON y ADDRESS **contra cualquier dato "
        f"personal anotado** de al menos {100 * PRECISION_FLOOR:.0f} %; en empate, el más "
        "alto. Una detección solo cuenta como error si tapa texto que no es un dato personal. "
        "También se publican la precisión del mismo tipo y la estricta.",
        "",
        "Esta forma de medir el suelo se decidió el 2026-10-01, **después** de ver la primera "
        "ejecución en dev, donde la precisión del mismo tipo fue del 63\u201369 % (PERSON) y del "
        "56\u201358 % (ADDRESS) (ADR-0011, enmienda). El 85 % no ha cambiado.",
        "",
        render_selection(selection).rstrip("\n"),
    ]
    names = ("NOMBRE_SUJETO_ASISTENCIA", "NOMBRE_PERSONAL_SANITARIO")
    rss = ner.get("worker_rss_mb")
    load = ner.get("load_seconds")
    if ner.get("floor_met") is False:
        chosen = [
            "",
            f"**{FLOOR_NOT_MET}**",
            "",
            f"Ningún umbral llega al suelo: se usa el más preciso, **{ner.get('threshold')}** "
            "(el de mayor precisión contra cualquier dato personal en el tipo más débil; en "
            "empate, el más alto). Con él se mide la partición de test una sola vez. Activa el "
            "NER solo si te vale que tape de más texto que no es personal.",
        ]
    else:
        chosen = [
            "",
            f"**Umbral elegido: {ner.get('threshold')}** (cumple el suelo). Con él se mide la "
            "partición de test una sola vez.",
        ]
    person = report.by_type.get("PERSON", {})
    lines += [
        *chosen,
        "",
        "## Con NER: antes y después (test)",
        "",
        "| Medida | Sin NER | Con NER |",
        "|---|---|---|",
        f"| Fugas por cada 100 (todos los tipos) | {float(base.overall['leaks_per_100']):.1f} "
        f"| {float(report.overall['leaks_per_100']):.1f} |",
        f"| Fugas por cada 100 en nombres (paciente y personal sanitario) | "
        f"{_leaks(base, names)} | {_leaks(report, names)} |",
        f"| Fugas por cada 100 en CALLE | {_leaks(base, ('CALLE',))} "
        f"| {_leaks(report, ('CALLE',))} |",
        f"| Precisión de PERSON: cualquier dato personal / mismo tipo / estricta | — | "
        f"{_pct(person.get('any_pii_precision'))} / {_pct(person.get('precision'))} / "
        f"{_pct(person.get('strict_precision'))} |",
        f"| Detecciones de PERSON que tapan texto no personal | — | "
        f"{person.get('non_pii_fp', '—')} |",
        f"| Latencia p50 / p95 por documento | {base.latency_ms['p50']:.2f} ms / "
        f"{base.latency_ms['p95']:.2f} ms | {report.latency_ms['p50']:.0f} ms / "
        f"{report.latency_ms['p95']:.0f} ms (caché fría) |",
        f"| Latencia p50 / p95 con la caché caliente | — | {float(warm.get('p50', 0)):.2f} ms / "
        f"{float(warm.get('p95', 0)):.2f} ms |",
        f"| Memoria del proceso del NER (RSS) | — | "
        f"{f'{rss:.0f} MB' if isinstance(rss, int | float) else '—'} |",
        f"| Arranque del pool (SHA-256 del modelo y carga) | — | "
        f"{f'{load:.0f} s' if isinstance(load, int | float) else '—'} |",
        "",
        "La caché fría no es fría del todo en un documento: antes de medir, la evaluación "
        "pasa el primero una vez sin cronometrar (calentamiento), así que 1 de las "
        f"{report.documents} medidas ya sale de la caché.",
        "",
        f"Modelo `{ner.get('model')}` en el commit `{str(ner.get('revision'))[:12]}`, "
        f"manifiesto SHA-256 `{str(ner.get('manifest_sha256'))[:12]}`.",
        "",
    ]
    tables = render_markdown(report).replace("## ", "### Con NER · ")
    return "\n".join(lines) + "\n" + tables


def _open_ner(model_dir: Path, threads: int) -> tuple[NerPool, Manifest]:  # pragma: no cover
    """The same pool as the gateway (worker process, manifest checked twice), one worker."""
    manifest = load_manifest()
    verify_model_dir(model_dir, manifest)
    pool = NerPool(
        GLINER_FACTORY,
        {"model_dir": str(model_dir), "threads": threads},
        workers=1,
        timeout=600,
        verify={
            "model_dir": str(model_dir),
            "manifest": str(DEFAULT_MANIFEST),
            "digest": manifest.digest,
        },
    )
    pool.start()
    return pool, manifest


def _model_info(manifest: Manifest, threads: int) -> dict[str, object]:
    return {
        "model": manifest.model,
        "revision": manifest.revision,
        "manifest_sha256": manifest.digest,
        "torch_threads": threads or f"torch default ({os.cpu_count()} logical CPUs)",
        "workers": 1,
    }


def save_dev_selection(document: Mapping[str, object]) -> str:
    """evals/results/<date>-<version>-ner-dev.json; its name."""
    environment = document["environment"]
    if not isinstance(environment, dict):
        raise TypeError("the dev document needs its environment")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{environment['date']}-{environment['antifaz']}-ner-dev.json"
    text = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    (RESULTS_DIR / name).write_text(text, encoding="utf-8", newline="\n")
    return name


# --- Presidio baseline (issue 13) --------------------------------------------------------------

MEDDOCAN_TEST = "MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)"
SYNTHETIC_DATASET = "synthetic-v1 (evals/datasets/synthetic-v1.jsonl)"
# The synthetic set is labelled directly with Antifaz types.
SYNTHETIC_MAPPING: dict[str, EntityType | None] = {t.value: t for t in EntityType}
NOT_FOUND = {"detections": 0, "overlapping_gold": 0}
ANTIFAZ_WITHOUT_NER = "Antifaz sin NER"
ANTIFAZ_WITH_NER = "Antifaz con NER"
SYNTHETIC_BIAS = "Conjunto escrito por el mismo equipo que Antifaz: favorece a Antifaz."
OTHER_NER_ENGINES = (
    "Presidio admite otros motores de NER (transformers, GLiNER) con modelos en español con "
    "licencia permisiva que aquí no se han medido."
)


def load_report(path: Path) -> Report:
    """A report written by to_json (an older one without the newer fields gets their default)."""
    return Report(**json.loads(path.read_text(encoding="utf-8")))


def latest_report(results_dir: Path, suffix: str) -> tuple[Report, str] | None:
    """The newest <date>-<version><suffix>.json of results_dir (by name: the date comes first)
    and its name, or None. "-ner" does not match the "-ner-dev" tables."""
    names = sorted(path.name for path in results_dir.glob(f"*{suffix}.json"))
    return (load_report(results_dir / names[-1]), names[-1]) if names else None


def presidio_mapping(mapping: Mapping[str, EntityType | None]) -> dict[str, EntityType | None]:
    """The dataset mapping as Presidio sees it: only the types Presidio also covers keep their
    type. CALLE (ADDRESS) or ID_ASEGURAMIENTO (ES_NSS) are "not covered" for Presidio, so its
    covered leaks and per-label rows only speak of what it looks for."""
    both = set(presidio_baseline.BOTH_COVER)
    return {label: (t if t in both else None) for label, t in mapping.items()}


def shared_types(
    documents: Sequence[Document], mapping: Mapping[str, EntityType | None]
) -> list[str]:
    """The types both Antifaz and Presidio cover that the documents really annotate (through
    mapping), in the order of presidio_baseline.BOTH_COVER."""
    annotated = {mapping.get(a.label) for d in documents for a in d.annotations}
    return [entity.value for entity in presidio_baseline.BOTH_COVER if entity in annotated]


def run_presidio(
    documents: Sequence[Document],
    analyzer: presidio_baseline.Analyzer,
    *,
    versions: Mapping[str, str],
    configuration: Mapping[str, object],
    mapping: Mapping[str, EntityType | None] = meddocan.MEDDOCAN_TO_ANTIFAZ_NER,
    dataset: str = MEDDOCAN_TEST,
) -> Report:
    """Presidio on `documents` with the same evaluate() as Antifaz. On MEDDOCAN names map to
    PERSON (the NER mapping), so PERSON can be compared with Antifaz's NER."""
    report = evaluate(
        documents, presidio_mapping(mapping), presidio_baseline.detector(analyzer), dataset=dataset
    )
    report.baseline = {
        "tool": "presidio-analyzer",
        "versions": dict(versions),
        "configuration": dict(configuration),
        "shared_types": shared_types(documents, mapping),
    }
    return report


def _labels_of(entity: str, mapping: Mapping[str, EntityType | None]) -> list[str]:
    return [label for label, mapped in mapping.items() if mapped is not None and mapped == entity]


def _tool_row(report: Report | None, entity: str, covers: bool, labels: Sequence[str]) -> list[str]:
    """Recall, precision (same type), precision (any personal data), strict F1, leaks per 100."""
    if report is None:
        return ["sin resultados"] * 5
    leaked = _leaks(report, labels)
    if not covers:
        return ["no aplica (no busca este tipo)", "—", "—", "—", leaked]
    metrics = report.by_type.get(entity, {})
    return [
        _pct(metrics.get("recall")),
        _pct(metrics.get("precision")),
        _pct(metrics.get("any_pii_precision")),
        _pct(metrics.get("strict_f1")),
        leaked,
    ]


def _shared_table(
    presidio: Report,
    tools: Sequence[tuple[str, Report | None]],
    mapping: Mapping[str, EntityType | None],
) -> list[str]:
    shared = presidio.baseline.get("shared_types")
    shared = [str(entity) for entity in shared] if isinstance(shared, list) else []
    lines = [
        "| Tipo | Herramienta | Datos | Recall (solape) | Precisión (mismo tipo) "
        "| Precisión (cualquier dato personal) | F1 estricto | Fugas por cada 100 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entity in shared:
        labels = _labels_of(entity, mapping)
        rows = [presidio.by_source_label.get(label) for label in labels]
        total = sum(int(row["n"] or 0) for row in rows if row is not None)
        for name, report in tools:
            covers = not (name == ANTIFAZ_WITHOUT_NER and entity == EntityType.PERSON.value)
            cells = " | ".join(_tool_row(report, entity, covers, labels))
            lines.append(f"| {entity} | {name} | {total} | {cells} |")
    return lines


def _ms(report: Report | None) -> str:
    if report is None:
        return "sin resultados | sin resultados"
    return f"{report.latency_ms['p50']:.2f} ms | {report.latency_ms['p95']:.2f} ms"


def _found(report: Report, entity: str) -> str:
    counts = report.unmatched_types.get(entity, NOT_FOUND)
    return f"{counts['detections']} ({counts['overlapping_gold']})"


def _overall(report: Report | None, key: str) -> str:
    return "sin resultados" if report is None else f"{float(report.overall[key]):.1f}"


def _setup_lines(presidio: Report, ner: Report | None, ner_name: str | None) -> list[str]:
    versions = presidio.baseline.get("versions")
    versions = versions if isinstance(versions, dict) else {}
    configuration = presidio.baseline.get("configuration")
    configuration = configuration if isinstance(configuration, dict) else {}
    recognizers = ", ".join(f"`{name}`" for name in configuration.get("recognizers", []))
    regions = ", ".join(configuration.get("phone_regions", []))
    commit = presidio.environment.get("commit", "unknown")
    if ner is not None and ner_name:
        ner_source = (
            f"`evals/results/{ner_name}` (commit `{ner.environment.get('commit', 'unknown')}`; "
            "no se vuelve a ejecutar)"
        )
    else:
        ner_source = "no hay resultados con NER en `evals/results/`"
    return [
        "### Configuración de Presidio",
        "",
        f"- **Versiones:** presidio-analyzer {versions.get('presidio-analyzer', '—')}, spaCy "
        f"{versions.get('spacy', '—')} y el modelo `{presidio_baseline.MODEL}` "
        f"{versions.get(presidio_baseline.MODEL_PACKAGE, '—')} (MIT, multilingüe, entrenado con "
        f"WikiNER); SHA-256 de su rueda `{str(versions.get('model_sha256', '—'))[:12]}…`, fijado "
        "en `uv.lock`.",
        f"- **Idioma:** `{configuration.get('language', '—')}` en el motor NLP, en el registro de "
        "reconocedores y en el analizador. Si falta un reconocedor en español o alguna pieza no es "
        "española, el script se para en vez de seguir en inglés sin avisar.",
        f"- **Reconocedores:** {recognizers}. Son todos los que Presidio activa por defecto "
        "(`default_recognizers.yaml`) y sirven para cualquier idioma o para español, más el de "
        "pasaporte español, que Presidio trae desactivado. Teléfonos con región "
        f"{regions}; tarjetas con las palabras de contexto en español de esa misma configuración.",
        f"- **Umbral de puntuación:** {configuration.get('score_threshold', '—')} (el de Presidio "
        "por defecto): cuenta cada resultado.",
        "- **NER:** los ajustes de `spacy_multilingual.yaml` de Presidio (PER, LOC y ORG) con este "
        "modelo. Los modelos de spaCy en español (`es_core_news_*`) son GPL-3.0 y no se usan.",
        "- **Tipos de Presidio -> Antifaz:** EMAIL_ADDRESS -> EMAIL, PHONE_NUMBER -> PHONE, "
        "IBAN_CODE -> IBAN, CREDIT_CARD -> CREDIT_CARD, IP_ADDRESS -> IP, ES_NIF -> ES_DNI, "
        "ES_NIE -> ES_NIE, ES_PASSPORT -> ES_PASSPORT, PERSON -> PERSON. LOCATION, ORGANIZATION, "
        "DATE_TIME, URL, CRYPTO y MAC_ADDRESS no tienen equivalente: no se comparan, pero tapan "
        "texto y cuentan para las fugas, como cualquier detección.",
        f"- **Antifaz sin NER y Presidio:** medidos en esta ejecución (commit `{commit}`). "
        f"**{ANTIFAZ_WITH_NER}:** {ner_source}.",
    ]


def _global_lines(presidio: Report, base: Report, ner: Report | None) -> list[str]:
    """Leaks over every annotated value, per tool, a plain sentence, and the labels outside the
    shared types (where the totals differ)."""
    p, a = float(presidio.overall["leaks_per_100"]), float(base.overall["leaks_per_100"])
    relation = "menos" if p < a else "las mismas" if p == a else "más"
    sentence = f"Presidio deja {relation} fugas en total que {ANTIFAZ_WITHOUT_NER}"
    if ner is not None:
        n = float(ner.overall["leaks_per_100"])
        sentence += f" y {'menos' if p < n else 'las mismas' if p == n else 'más'} que "
        sentence += ANTIFAZ_WITH_NER
    lines = [
        "### Todos los datos anotados",
        "",
        "Fugas por cada 100 datos de **todas** las etiquetas de MEDDOCAN, también las que no "
        "entran en la comparación, y sobre los tipos que busca cada herramienta.",
        "",
        "| Herramienta | Fugas por cada 100 (todos los datos) | Fugas por cada 100 (tipos que "
        "busca) |",
        "|---|---|---|",
        f"| {ANTIFAZ_WITHOUT_NER} | {_overall(base, 'leaks_per_100')} | "
        f"{_overall(base, 'covered_leaks_per_100')} |",
        f"| {ANTIFAZ_WITH_NER} | {_overall(ner, 'leaks_per_100')} | "
        f"{_overall(ner, 'covered_leaks_per_100')} |",
        f"| Presidio | {_overall(presidio, 'leaks_per_100')} | "
        f"{_overall(presidio, 'covered_leaks_per_100')} |",
        "",
        f"**{sentence}.** La cifra total no coincide con la de los tipos comparados porque una "
        "fuga se evita con una detección de cualquier tipo: las detecciones de Presidio sin tipo "
        "en Antifaz (LOCATION, ORGANIZATION, DATE_TIME, URL…) también tapan datos que no entran "
        "en la comparación, como TERRITORIO, PAIS, HOSPITAL o FECHAS. Por etiqueta:",
        "",
        "| Etiqueta de MEDDOCAN | Datos | Fugas por cada 100: Antifaz sin NER | Antifaz con NER "
        "| Presidio |",
        "|---|---|---|---|---|",
    ]
    shared = presidio.baseline.get("shared_types")
    shared = set(shared) if isinstance(shared, list) else set()
    for label, row in presidio.by_source_label.items():
        mapped = meddocan.MEDDOCAN_TO_ANTIFAZ_NER.get(label)
        if mapped is not None and mapped.value in shared:
            continue
        ner_cell = _leaks(ner, (label,)) if ner is not None else "sin resultados"
        lines.append(
            f"| {label} | {row['n']} | {_leaks(base, (label,))} | {ner_cell} | "
            f"{_leaks(presidio, (label,))} |"
        )
    return lines


def _synthetic_lines(synthetic: Report, synthetic_base: Report) -> list[str]:
    return [
        "",
        "### Conjunto sintético",
        "",
        f"**{SYNTHETIC_BIAS}** El generador (`evals/generate.py`) y el detector de Antifaz los "
        "escribió el mismo equipo, con los formatos españoles que Antifaz busca; por eso esta "
        "tabla va aparte y no se suma a la de MEDDOCAN. Añade los tipos que MEDDOCAN no tiene. Sin "
        "NER (el conjunto no anota nombres).",
        "",
        *_shared_table(
            synthetic,
            ((ANTIFAZ_WITHOUT_NER, synthetic_base), ("Presidio", synthetic)),
            SYNTHETIC_MAPPING,
        ),
        "",
        "| Herramienta | Fugas por cada 100 (todos los datos del conjunto) |",
        "|---|---|",
        f"| {ANTIFAZ_WITHOUT_NER} | {_overall(synthetic_base, 'leaks_per_100')} |",
        f"| Presidio | {_overall(synthetic, 'leaks_per_100')} |",
    ]


def render_presidio_markdown(
    presidio: Report,
    base: Report,
    ner: Report | None,
    ner_name: str | None,
    synthetic: Report | None = None,
    synthetic_base: Report | None = None,
) -> str:
    """The "Comparación con Presidio" section: the setup, then Antifaz without NER, Antifaz with
    NER (the latest committed results, not run again) and Presidio on the types both cover and
    MEDDOCAN annotates, the leaks over every annotated value and, apart and with its bias
    stated, the synthetic set. Numbers only, no adjectives."""
    tools = ((ANTIFAZ_WITHOUT_NER, base), (ANTIFAZ_WITH_NER, ner), ("Presidio", presidio))
    lines = [
        "## Comparación con Presidio",
        "",
        "Mismo script, mismos datos (partición de test de MEDDOCAN) y mismas métricas que el resto "
        "de esta página. Solo se comparan los tipos que **cubren los dos** y que MEDDOCAN anota.",
        "",
        *_setup_lines(presidio, ner, ner_name),
        "",
        "### Tipos que cubren los dos",
        "",
        *_shared_table(presidio, tools, meddocan.MEDDOCAN_TO_ANTIFAZ_NER),
        "",
        "Datos de cada tipo en MEDDOCAN: EMAIL = CORREO_ELECTRONICO; PHONE = NUMERO_TELEFONO y "
        "NUMERO_FAX; PERSON = NOMBRE_SUJETO_ASISTENCIA y NOMBRE_PERSONAL_SANITARIO. Una fuga se "
        "evita con una detección de **cualquier** tipo de la misma herramienta (ADR-0011).",
        "",
        *_global_lines(presidio, base, ner),
        "",
        "### Tipos que cubren los dos y MEDDOCAN no anota",
        "",
        "No aplica: MEDDOCAN no tiene estos datos, así que no hay recall ni fugas que medir. Cada "
        "detección de estos tipos cae sobre otro texto; entre paréntesis, cuántas tocan un dato "
        "anotado de otra etiqueta.",
        "",
        "| Tipo | Antifaz sin NER: detecciones | Presidio: detecciones |",
        "|---|---|---|",
    ]
    shared = presidio.baseline.get("shared_types")
    shared = shared if isinstance(shared, list) else []
    for entity_type in presidio_baseline.BOTH_COVER:
        if entity_type.value not in shared:
            lines.append(
                f"| {entity_type.value} | {_found(base, entity_type.value)} "
                f"| {_found(presidio, entity_type.value)} |"
            )
    lines += [
        "",
        "### Detecciones de Presidio sin tipo en Antifaz",
        "",
        "| Tipo de Presidio | Detecciones | Tocan un dato anotado |",
        "|---|---|---|",
    ]
    for presidio_type, mapped in presidio_baseline.PRESIDIO_TO_ANTIFAZ.items():
        if mapped is None:
            counts = presidio.unmatched_types.get(presidio_type, NOT_FOUND)
            lines.append(
                f"| {presidio_type} | {counts['detections']} | {counts['overlapping_gold']} |"
            )
    lines += [
        "",
        "### Latencia por documento (CPU)",
        "",
        "| Herramienta | p50 | p95 |",
        "|---|---|---|",
        f"| {ANTIFAZ_WITHOUT_NER} | {_ms(base)} |",
        f"| {ANTIFAZ_WITH_NER} (caché fría) | {_ms(ner)} |",
        f"| Presidio | {_ms(presidio)} |",
    ]
    if synthetic is not None and synthetic_base is not None:
        lines += _synthetic_lines(synthetic, synthetic_base)
    lines += [
        "",
        "### Límites",
        "",
        "- MEDDOCAN es texto clínico: tiene emails, teléfonos y nombres, pero ningún DNI, NIE, "
        "IBAN, tarjeta, pasaporte ni IP: en MEDDOCAN la comparación se queda en tres tipos.",
        "- El NER de Presidio aquí es un modelo multilingüe pequeño de spaCy, entrenado con "
        "Wikipedia y no con texto clínico; los modelos de spaCy en español son GPL-3.0. "
        f"{OTHER_NER_ENGINES} El NER de Antifaz es otro modelo (GLiNER), opcional y apagado por "
        "defecto.",
        "- Las cifras miden esta configuración en estos conjuntos de datos, nada más.",
        "- La latencia de Antifaz con NER viene de otra ejecución (otro momento, misma máquina).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: download MEDDOCAN if needed, evaluate both sets (and, with
    --ner, the NER) and write the results. Every file is written at the END of the run (a
    tracked file written earlier would mark the next report's commit as "-dirty")."""
    parser = argparse.ArgumentParser(description="Run Antifaz-Bench (MEDDOCAN + synthetic).")
    parser.add_argument("--no-doc", action="store_true", help="do not update docs/benchmark.md")
    parser.add_argument("--ner", action="store_true", help="also measure the NER model")
    parser.add_argument(
        "--dev-only", action="store_true", help="with the NER: only the dev threshold table"
    )
    parser.add_argument("--threads", type=int, default=0, help="torch threads with --ner")
    parser.add_argument(
        "--presidio",
        action="store_true",
        help="also measure Presidio on MEDDOCAN test (needs the bench dependency group)",
    )
    args = parser.parse_args(argv)
    if args.presidio and (args.ner or args.dev_only):
        # The bench group and the ner extra are never installed together (pyproject.toml).
        parser.error("--presidio reads the NER results from evals/results/; run it without --ner")
    ner_on = args.ner or args.dev_only

    archive = meddocan.download()
    synthetic = report = None
    synthetic_documents = from_jsonl(SYNTHETIC.read_text(encoding="utf-8"))
    if not args.dev_only:
        synthetic = run_synthetic(synthetic_documents)
        report = evaluate(
            meddocan.load_test(archive),
            meddocan.MEDDOCAN_TO_ANTIFAZ,
            dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)",
        )
        report.environment["dataset_md5"] = meddocan.MD5
    presidio_report = presidio_synthetic = None
    if args.presidio:  # pragma: no cover - needs the bench group (make bench PRESIDIO=1)
        try:
            analyzer = presidio_baseline.build_analyzer()
            versions = presidio_baseline.versions()
        except (ImportError, presidio_baseline.PresidioSetupError) as error:
            print(f"Presidio baseline not run: {error}", file=sys.stderr)
            return 2
        presidio_report = run_presidio(
            meddocan.load_test(archive),
            analyzer,
            versions=versions,
            configuration=presidio_baseline.configuration(),
        )
        presidio_report.environment["dataset_md5"] = meddocan.MD5
        presidio_synthetic = run_presidio(
            synthetic_documents,
            analyzer,
            versions=versions,
            configuration=presidio_baseline.configuration(),
            mapping=SYNTHETIC_MAPPING,
            dataset=SYNTHETIC_DATASET,
        )
    ner_report = None
    dev_document: dict[str, object] | None = None
    if ner_on:  # pragma: no cover - needs the model (make ner-model)
        model_dir = Settings().ner_model_dir
        if model_dir is None:
            print(
                "Set ANTIFAZ_NER_MODEL_DIR (make ner-model downloads the model).", file=sys.stderr
            )
            return 2
        opening = time.perf_counter()
        pool, manifest = _open_ner(model_dir, args.threads)
        # Start of the pool: both SHA-256 checks of the model files and the model load.
        load_seconds = round(time.perf_counter() - opening, 1)
        try:
            rows = dev_selection(meddocan.load_dev(archive), pool, model_id=manifest.digest)
            dev_document = {
                "dataset": "MEDDOCAN dev (Zenodo 10.5281/zenodo.4279323)",
                "environment": {**_environment(), "dataset_md5": meddocan.MD5},
                "precision_floor": PRECISION_FLOOR,
                "floor_types": list(FLOOR_TYPES),
                "selection_rule": SELECTION_RULE,
                "selection_rule_changed": SELECTION_RULE_CHANGED,
                "thresholds": list(THRESHOLDS),
                **_model_info(manifest, args.threads),
                "rows": rows,
            }
            print(render_selection(rows))
            chosen, floor_met = select_threshold(rows)
            dev_document["chosen_threshold"] = chosen
            dev_document["floor_met"] = floor_met
            if not floor_met:
                print(FLOOR_NOT_MET, file=sys.stderr)
                print(f"Fallback threshold (most precise): {chosen}", file=sys.stderr)
            if args.dev_only:
                print(f"Chosen threshold on dev: {chosen} (floor met: {floor_met})")
                print(f"Dev table: evals/results/{save_dev_selection(dev_document)}")
                return 0 if floor_met else 3
            ner_report, warm = measure_test(
                meddocan.load_test(archive), pool, chosen, manifest.digest
            )
            rss = [rss_mb(pid) for pid in pool.worker_pids() if pid is not None]
        finally:
            pool.close()
        ner_report.environment["dataset_md5"] = meddocan.MD5
        ner_report.ner = {
            "threshold": chosen,
            "floor_met": floor_met,
            "precision_floor": PRECISION_FLOOR,
            "selection_rule": SELECTION_RULE,
            "selection_rule_changed": SELECTION_RULE_CHANGED,
            "selection": rows,
            "selection_split": "MEDDOCAN dev",
            "latency_warm_ms": warm,
            "worker_rss_mb": rss[0] if rss else None,
            "load_seconds": load_seconds,
            **_model_info(manifest, args.threads),
        }
    if report is None or synthetic is None:  # only --dev-only gets here without them
        return 0
    synthetic_name = _save(synthetic, "-synthetic")
    name = _save(report, "")
    table = render_markdown(report)
    names = [f"evals/results/{name}", f"evals/results/{synthetic_name}"]
    if ner_report is not None and dev_document is not None:
        names.append(f"evals/results/{_save(ner_report, '-ner')}")
        names.append(f"evals/results/{save_dev_selection(dev_document)}")
    presidio_table = None
    if presidio_report is not None:  # pragma: no cover - needs the bench group
        found = latest_report(RESULTS_DIR, "-ner")
        ner_previous, ner_name = found if found else (None, None)
        presidio_table = render_presidio_markdown(
            presidio_report, report, ner_previous, ner_name, presidio_synthetic, synthetic
        )
        names.append(f"evals/results/{_save(presidio_report, '-presidio')}")
        if presidio_synthetic is not None:
            names.append(f"evals/results/{_save(presidio_synthetic, '-presidio-synthetic')}")
    if not args.no_doc:
        update_benchmark_doc(BENCHMARK_DOC, table)
        update_benchmark_doc(
            BENCHMARK_DOC, render_markdown(synthetic), SYNTHETIC_START, SYNTHETIC_END
        )
        if ner_report is not None:
            update_benchmark_doc(
                BENCHMARK_DOC, render_ner_markdown(ner_report, report), NER_START, NER_END
            )
        if presidio_table is not None:  # pragma: no cover - needs the bench group
            update_benchmark_doc(BENCHMARK_DOC, presidio_table, PRESIDIO_START, PRESIDIO_END)
    print(table)
    if ner_report is not None:
        print(render_ner_markdown(ner_report, report))
    if presidio_table is not None:  # pragma: no cover - needs the bench group
        print(presidio_table)
    print("Results: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
