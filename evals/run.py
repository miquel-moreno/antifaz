"""Run Antifaz-Bench on MEDDOCAN and write the report (labels, counts and metrics only).

With --ner (`make bench NER=1`) it also runs the NER model (`make ner-model` first): the
threshold is chosen on the MEDDOCAN dev split, then the test split is measured ONCE with it,
with the cache cold and warm. The report records the model revision and the manifest hash.
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
from evals.datasets import meddocan
from evals.datasets.meddocan import Document
from evals.generate import from_jsonl
from evals.metrics import (
    Annotation,
    Counts,
    latency_ms,
    leaks,
    leaks_per_100,
    overlap_counts,
    strict_counts,
)

NER_START = "<!-- bench-ner:start -->"
NER_END = "<!-- bench-ner:end -->"
# Candidate NER thresholds, and the least precision (overlap) PERSON and ADDRESS must keep on
# the dev split. Fixed before looking at any result: the floor stops a low threshold from
# "winning" by masking half the text.
THRESHOLDS = (0.3, 0.4, 0.5, 0.6)
PRECISION_FLOOR = 0.85
START_MARKER = "<!-- bench:start -->"
END_MARKER = "<!-- bench:end -->"
SYNTHETIC_START = "<!-- bench-synthetic:start -->"
SYNTHETIC_END = "<!-- bench-synthetic:end -->"
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
        "strict_precision", "strict_recall", "strict_f1"}.
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
    detect: Callable[[str], list[Span]] = scan,
    *,
    dataset: str = "custom documents",
) -> Report:
    """Run detect on every document and compare it with the gold annotations."""
    overlap: dict[str, Counts] = defaultdict(Counts)
    strict: dict[str, Counts] = defaultdict(Counts)
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
        predicted = [Annotation(s.start, s.end, s.type.value) for s in spans]
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
        o, s = overlap[label], strict[label]
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
        "| Tipo Antifaz | Precisión | Recall | F1 | F1 estricto |",
        "|---|---|---|---|---|",
    ]
    for label, metrics in report.by_type.items():
        lines.append(
            f"| {label} | {_pct(metrics['precision'])} | {_pct(metrics['recall'])} "
            f"| {_pct(metrics['f1'])} | {_pct(metrics['strict_f1'])} |"
        )
    lines += [
        "",
        "## Tipos aún no cubiertos",
        "",
        "Antifaz todavía no busca estos datos (la mayoría necesitan NER, issue 6). Se miden",
        "igual: sus fugas cuentan en la cifra global.",
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
    mapping = {entity_type.value: entity_type for entity_type in EntityType}
    return evaluate(documents, mapping, dataset="synthetic-v1 (evals/datasets/synthetic-v1.jsonl)")


@dataclass
class NerBench:
    report: Report  # test split, chosen threshold, cache cold
    threshold: float
    selection: list[dict[str, object]]  # one row per candidate threshold, dev split
    warm_latency_ms: dict[str, float]  # the same test documents again: every text cached


class NoThresholdError(ValueError):
    """No candidate threshold keeps the pre-registered precision floor on dev."""


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
        row |= {
            f"{entity}_precision": precision,
            f"{entity}_recall": float(metrics.get("recall", 0.0)),
            f"{entity}_strict_precision": float(metrics.get("strict_precision", 0.0)),
            f"{entity}_strict_recall": float(metrics.get("strict_recall", 0.0)),
            f"{entity}_tp": tp,
            f"{entity}_fp": fp,
            f"{entity}_fn": fn,
            f"{entity}_gold": tp + fn,
            f"{entity}_meets_floor": precision >= floor,
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
    (overlap) both reach `floor`; a tie goes to the higher threshold (fewer false positives).
    NoThresholdError says, for each threshold, which type falls short."""
    good = [
        row for row in rows if all(_number(row, f"{e}_precision") >= floor for e in FLOOR_TYPES)
    ]
    if not good:
        failing = "; ".join(
            f"{row['threshold']}: "
            + ", ".join(
                f"{entity} {100 * _number(row, f'{entity}_precision'):.1f} %"
                for entity in FLOOR_TYPES
                if _number(row, f"{entity}_precision") < floor
            )
            for row in rows
        )
        raise NoThresholdError(
            f"no threshold keeps the precision floor of {100 * floor:.0f} % ({failing})"
        )
    best = min(good, key=lambda r: (_number(r, "covered_leaks_per_100"), -_number(r, "threshold")))
    return _number(best, "threshold")


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
    """Choose the threshold on `dev`, then measure `test` once with it (cold, then warm)."""
    rows = dev_selection(dev, predictor, thresholds, model_id=model_id, floor=floor)
    chosen = choose_threshold(rows, floor)
    report, warm = measure_test(test, predictor, chosen, model_id)
    return NerBench(report, chosen, rows, warm)


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
    return f"{100 * float(value):.1f} %"  # type: ignore[arg-type]


def _leaks(report: Report, labels: Sequence[str]) -> str:
    rows = [report.by_source_label[label] for label in labels if label in report.by_source_label]
    total = sum(int(row["n"] or 0) for row in rows)
    leaked = sum(int(row["leaks"] or 0) for row in rows)
    return f"{leaks_per_100(leaked, total):.1f}"


def render_selection(rows: Sequence[Mapping[str, object]]) -> str:
    """The dev table as Markdown (also printed before choosing)."""
    lines = [
        "| Umbral | Fugas por cada 100 (todos) | Fugas (cubiertos) | Precisión PERSON "
        "| Recall PERSON | Precisión ADDRESS | Recall ADDRESS | Suelo PERSON / ADDRESS |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        floor = " / ".join("sí" if row[f"{e}_meets_floor"] else "no" for e in FLOOR_TYPES)
        lines.append(
            f"| {row['threshold']} | {_number(row, 'leaks_per_100'):.1f} | "
            f"{_number(row, 'covered_leaks_per_100'):.1f} | "
            f"{_pct_value(row['PERSON_precision'])} | {_pct_value(row['PERSON_recall'])} | "
            f"{_pct_value(row['ADDRESS_precision'])} | {_pct_value(row['ADDRESS_recall'])} | "
            f"{floor} |"
        )
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
        f"(tipos cubiertos) con precisión de PERSON y ADDRESS de al menos "
        f"{100 * PRECISION_FLOOR:.0f} %; en empate, el más alto.",
        "",
        render_selection(selection).rstrip("\n"),
    ]
    names = ("NOMBRE_SUJETO_ASISTENCIA", "NOMBRE_PERSONAL_SANITARIO")
    rss = ner.get("worker_rss_mb")
    lines += [
        "",
        f"**Umbral elegido: {ner.get('threshold')}.** Con él se mide la partición de test una "
        "sola vez.",
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
        f"| Latencia p50 / p95 por documento | {base.latency_ms['p50']:.2f} ms / "
        f"{base.latency_ms['p95']:.2f} ms | {report.latency_ms['p50']:.0f} ms / "
        f"{report.latency_ms['p95']:.0f} ms (caché fría) |",
        f"| Latencia p50 / p95 con la caché caliente | — | {float(warm.get('p50', 0)):.2f} ms / "
        f"{float(warm.get('p95', 0)):.2f} ms |",
        f"| Memoria del proceso del NER (RSS) | — | "
        f"{'—' if rss is None else f'{float(rss):.0f} MB'} |",  # type: ignore[arg-type]
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
    args = parser.parse_args(argv)
    ner_on = args.ner or args.dev_only

    archive = meddocan.download()
    synthetic = report = None
    if not args.dev_only:
        synthetic = run_synthetic(from_jsonl(SYNTHETIC.read_text(encoding="utf-8")))
        report = evaluate(
            meddocan.load_test(archive),
            meddocan.MEDDOCAN_TO_ANTIFAZ,
            dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)",
        )
        report.environment["dataset_md5"] = meddocan.MD5
    ner_report = None
    dev_document: dict[str, object] | None = None
    if ner_on:  # pragma: no cover - needs the model (make ner-model)
        model_dir = Settings().ner_model_dir
        if model_dir is None:
            print(
                "Set ANTIFAZ_NER_MODEL_DIR (make ner-model downloads the model).", file=sys.stderr
            )
            return 2
        pool, manifest = _open_ner(model_dir, args.threads)
        try:
            rows = dev_selection(meddocan.load_dev(archive), pool, model_id=manifest.digest)
            dev_document = {
                "dataset": "MEDDOCAN dev (Zenodo 10.5281/zenodo.4279323)",
                "environment": {**_environment(), "dataset_md5": meddocan.MD5},
                "precision_floor": PRECISION_FLOOR,
                "floor_types": list(FLOOR_TYPES),
                "thresholds": list(THRESHOLDS),
                **_model_info(manifest, args.threads),
                "rows": rows,
            }
            print(render_selection(rows))
            try:
                chosen = choose_threshold(rows)
            except NoThresholdError as error:
                name = save_dev_selection(dev_document)
                print(f"{error}\nDev table: evals/results/{name}", file=sys.stderr)
                return 3
            if args.dev_only:
                print(f"Chosen threshold on dev: {chosen}")
                print(f"Dev table: evals/results/{save_dev_selection(dev_document)}")
                return 0
            ner_report, warm = measure_test(
                meddocan.load_test(archive), pool, chosen, manifest.digest
            )
            rss = [rss_mb(pid) for pid in pool.worker_pids() if pid is not None]
        finally:
            pool.close()
        ner_report.environment["dataset_md5"] = meddocan.MD5
        ner_report.ner = {
            "threshold": chosen,
            "precision_floor": PRECISION_FLOOR,
            "selection": rows,
            "selection_split": "MEDDOCAN dev",
            "latency_warm_ms": warm,
            "worker_rss_mb": rss[0] if rss else None,
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
    if not args.no_doc:
        update_benchmark_doc(BENCHMARK_DOC, table)
        update_benchmark_doc(
            BENCHMARK_DOC, render_markdown(synthetic), SYNTHETIC_START, SYNTHETIC_END
        )
        if ner_report is not None:
            update_benchmark_doc(
                BENCHMARK_DOC, render_ner_markdown(ner_report, report), NER_START, NER_END
            )
    print(table)
    if ner_report is not None:
        print(render_ner_markdown(ner_report, report))
    print("Results: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
