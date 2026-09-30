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
    selection: list[dict[str, float]]  # one row per candidate threshold, dev split
    warm_latency_ms: dict[str, float]  # the same test documents again: every text cached


def _metric(report: Report, entity: str, name: str) -> float:
    return float(report.by_type.get(entity, {}).get(name, 0.0))


def selection_row(threshold: float, report: Report) -> dict[str, float]:
    return {
        "threshold": threshold,
        "leaks_per_100": float(report.overall["leaks_per_100"]),
        "covered_leaks_per_100": float(report.overall["covered_leaks_per_100"]),
        "person_precision": _metric(report, "PERSON", "precision"),
        "person_recall": _metric(report, "PERSON", "recall"),
        "address_precision": _metric(report, "ADDRESS", "precision"),
        "address_recall": _metric(report, "ADDRESS", "recall"),
    }


def choose_threshold(rows: Sequence[Mapping[str, object]], floor: float = PRECISION_FLOOR) -> float:
    """Fewest covered leaks per 100 among the thresholds whose PERSON and ADDRESS precision
    reach `floor`; a tie goes to the higher threshold (fewer false positives)."""
    good = [
        row
        for row in rows
        if float(row["person_precision"]) >= floor and float(row["address_precision"]) >= floor  # type: ignore[arg-type]
    ]
    if not good:
        raise ValueError(f"no threshold keeps the precision floor of {floor}")
    best = min(good, key=lambda r: (float(r["covered_leaks_per_100"]), -float(r["threshold"])))  # type: ignore[arg-type]
    return float(best["threshold"])  # type: ignore[arg-type]


def run_ner_bench(
    dev: Sequence[Document],
    test: Sequence[Document],
    make_detector: Callable[[float], NerDetector],
    thresholds: Sequence[float] = THRESHOLDS,
    floor: float = PRECISION_FLOOR,
) -> NerBench:
    """Choose the threshold on `dev`, then measure `test` once with it (cold, then warm)."""
    mapping = meddocan.MEDDOCAN_TO_ANTIFAZ_NER
    selection = []
    for threshold in thresholds:
        detect = Scanner(make_detector(threshold))
        selection.append(selection_row(threshold, evaluate(dev, mapping, detect)))
    chosen = choose_threshold(selection, floor)
    scanner = Scanner(make_detector(chosen))
    report = evaluate(
        test, mapping, scanner, dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)"
    )
    warm = evaluate(test, mapping, scanner)  # every text is in the cache now
    return NerBench(report, chosen, selection, warm.latency_ms)


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
        "| Umbral | Fugas por cada 100 (todos) | Fugas (cubiertos) | Precisión PERSON "
        "| Recall PERSON | Precisión ADDRESS | Recall ADDRESS |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in selection:
        lines.append(
            f"| {row['threshold']} | {row['leaks_per_100']:.1f} | "
            f"{row['covered_leaks_per_100']:.1f} | {_pct_value(row['person_precision'])} | "
            f"{_pct_value(row['person_recall'])} | {_pct_value(row['address_precision'])} | "
            f"{_pct_value(row['address_recall'])} |"
        )
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


def _ner_report(archive: Path, threads: int) -> Report:  # pragma: no cover - needs the model
    model_dir = Settings().ner_model_dir
    if model_dir is None:
        raise SystemExit("set ANTIFAZ_NER_MODEL_DIR (make ner-model downloads the model)")
    pool, manifest = _open_ner(model_dir, threads)
    try:

        def make(threshold: float) -> NerDetector:
            return NerDetector(
                pool, threshold=threshold, cache=SpanCache(10_000), model_id=manifest.digest
            )

        bench = run_ner_bench(meddocan.load_dev(archive), meddocan.load_test(archive), make)
        rss = [rss_mb(pid) for pid in pool.worker_pids() if pid is not None]
    finally:
        pool.close()
    bench.report.environment["dataset_md5"] = meddocan.MD5
    bench.report.ner = {
        "threshold": bench.threshold,
        "precision_floor": PRECISION_FLOOR,
        "selection": bench.selection,
        "selection_split": "MEDDOCAN dev",
        "latency_warm_ms": bench.warm_latency_ms,
        "model": manifest.model,
        "revision": manifest.revision,
        "manifest_sha256": manifest.digest,
        "worker_rss_mb": rss[0] if rss else None,
        "torch_threads": threads or f"torch default ({os.cpu_count()} logical CPUs)",
        "workers": 1,
    }
    return bench.report


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point: download MEDDOCAN if needed, evaluate both sets (and, with
    --ner, the NER) and write the results."""
    parser = argparse.ArgumentParser(description="Run Antifaz-Bench (MEDDOCAN + synthetic).")
    parser.add_argument("--no-doc", action="store_true", help="do not update docs/benchmark.md")
    parser.add_argument("--ner", action="store_true", help="also measure the NER model")
    parser.add_argument("--threads", type=int, default=0, help="torch threads with --ner")
    args = parser.parse_args(argv)

    synthetic = run_synthetic(from_jsonl(SYNTHETIC.read_text(encoding="utf-8")))
    archive = meddocan.download()
    report = evaluate(
        meddocan.load_test(archive),
        meddocan.MEDDOCAN_TO_ANTIFAZ,
        dataset="MEDDOCAN test (Zenodo 10.5281/zenodo.4279323)",
    )
    report.environment["dataset_md5"] = meddocan.MD5
    ner_report = _ner_report(archive, args.threads) if args.ner else None
    # Save only after every evaluation: writing a tracked results file earlier would make
    # a later report see a modified tree and mark its commit as "-dirty".
    synthetic_name = _save(synthetic, "-synthetic")
    name = _save(report, "")
    table = render_markdown(report)
    names = [f"evals/results/{name}", f"evals/results/{synthetic_name}"]
    if ner_report is not None:
        names.append(f"evals/results/{_save(ner_report, '-ner')}")
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
