"""Run Antifaz-Bench on MEDDOCAN and write the report (labels, counts and metrics only)."""

import argparse
import json
import platform
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from antifaz import __version__
from antifaz.detect.scan import scan
from antifaz.detect.types import EntityType, Span
from evals.datasets import meddocan
from evals.datasets.meddocan import Document
from evals.metrics import (
    Annotation,
    Counts,
    latency_ms,
    leaks,
    leaks_per_100,
    overlap_counts,
    strict_counts,
)

START_MARKER = "<!-- bench:start -->"
END_MARKER = "<!-- bench:end -->"
ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "evals" / "results"
BENCHMARK_DOC = ROOT / "docs" / "benchmark.md"


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
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, fixed args
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
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

    for document in documents:
        started = time.perf_counter_ns()
        spans = detect(document.text)
        samples.append(time.perf_counter_ns() - started)
        predicted = [Annotation(s.start, s.end, s.type.value) for s in spans]
        gold = _mapped(document.annotations, mapping)
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
    mapped_types = {t.value for t in mapping.values() if t is not None}
    for label in sorted(set(overlap) & (mapped_types | {s for s in overlap if overlap[s].tp})):
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


def update_benchmark_doc(doc: Path, table: str) -> None:
    """Replace what is between START_MARKER and END_MARKER with table.strip(), keeping
    the markers on their own lines. Raises ValueError (file untouched) if a marker is
    missing."""
    text = doc.read_text(encoding="utf-8")
    start, end = text.find(START_MARKER), text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{doc.name}: benchmark markers not found")
    new = text[: start + len(START_MARKER)] + "\n" + table.strip() + "\n" + text[end:]
    doc.write_text(new, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: download MEDDOCAN if needed, evaluate, write the results."""
    parser = argparse.ArgumentParser(description="Run Antifaz-Bench on the MEDDOCAN test set.")
    parser.add_argument("--no-doc", action="store_true", help="do not update docs/benchmark.md")
    args = parser.parse_args(argv)

    archive = meddocan.download()
    report = evaluate(
        meddocan.load_test(archive),
        meddocan.MEDDOCAN_TO_ANTIFAZ,
        dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)",
    )
    report.environment["dataset_md5"] = meddocan.MD5

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{report.environment['date']}-{report.environment['antifaz']}.json"
    (RESULTS_DIR / name).write_text(to_json(report), encoding="utf-8", newline="\n")
    table = render_markdown(report)
    if not args.no_doc:
        update_benchmark_doc(BENCHMARK_DOC, table)
    print(table)
    print(f"Results: evals/results/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
