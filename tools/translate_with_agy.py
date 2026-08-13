#!/usr/bin/env python3
"""Checkpointed full-script translation through Google Antigravity CLI.

The model receives script text inline and therefore needs no file or command
tools.  Each successful structured response is validated and atomically saved.
Existing batch files are treated as completed checkpoints on restart.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable


RAW_TOKEN = re.compile(r"\{\{RAW:[0-9A-F]+\}\}")
CONTROL_RUN = re.compile(r"(?:(?:\{\{RAW:[0-9A-F]+\}\})|\^|@n)+")
PLACEHOLDER = re.compile(r"⟦[0-9]+⟧")
QUOTA_RESET = re.compile(
    r"Individual quota reached.*?Resets in "
    r"(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)",
    re.DOTALL,
)


def load_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_completed(paths: Iterable[Path]) -> set[str]:
    completed: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            completed.update(
                item["id"] for item in payload
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            )
    return completed


def make_batches(
    records: list[dict[str, object]], max_chars: int, max_records: int
) -> Iterable[list[dict[str, object]]]:
    batch: list[dict[str, object]] = []
    characters = 0
    for record in records:
        size = len(str(record["source_template"]))
        if batch and (characters + size > max_chars or len(batch) >= max_records):
            yield batch
            batch = []
            characters = 0
        batch.append(record)
        characters += size
    if batch:
        yield batch


def mask_controls(template: str) -> tuple[str, dict[str, str]]:
    controls: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"⟦{len(controls)}⟧"
        controls[token] = match.group(0)
        return token

    return CONTROL_RUN.sub(replace, template), controls


def relevant_glossary(glossary: dict[str, object], records: list[dict[str, object]]) -> list[dict[str, str]]:
    corpus = "\n".join(str(record["source_text"]) for record in records)
    matches: list[dict[str, str]] = []
    for category in ("characters", "classes", "terms"):
        for entry in glossary.get(category, []):
            if entry["source"] in corpus:
                matches.append({"source": entry["source"], "target": entry["target"]})
    return matches


def prompt_for(
    rules: str, glossary: dict[str, object], records: list[dict[str, object]]
) -> tuple[str, dict[str, dict[str, str]]]:
    control_maps: dict[str, dict[str, str]] = {}
    compact = []
    for record in records:
        masked, controls = mask_controls(str(record["source_template"]))
        control_maps[str(record["id"])] = controls
        compact.append(
            {
                "id": record["id"],
                "script": record["script"],
                "page": record["page"],
                "text": masked,
            }
        )
    prompt = (
        rules
        + "\n本批固定译名："
        + json.dumps(relevant_glossary(glossary, records), ensure_ascii=False, separators=(",", ":"))
        + "\n记录按剧情顺序排列：\n"
        + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    )
    return prompt, control_maps


def restore_controls(
    translated: list[dict[str, object]], control_maps: dict[str, dict[str, str]]
) -> list[str]:
    failures: list[str] = []
    for result in translated:
        identifier = str(result.get("id", ""))
        text = result.get("translation")
        controls = control_maps.get(identifier)
        if not isinstance(text, str) or controls is None:
            failures.append(identifier or "<missing-id>")
            continue
        if PLACEHOLDER.findall(text) != list(controls):
            failures.append(identifier)
            continue
        for token, original in controls.items():
            text = text.replace(token, original)
        result["translation"] = text
        result["notes"] = ""
    return failures


def partition_response(
    source: list[dict[str, object]],
    translated: list[dict[str, object]],
    control_maps: dict[str, dict[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Keep every independently valid record and isolate unsafe ones."""
    source_by_id = {str(item["id"]): item for item in source}
    result_by_id: dict[str, dict[str, object]] = {}
    unsafe = set(source_by_id)
    reasons: list[str] = []
    for result in translated:
        identifier = str(result.get("id", ""))
        if identifier not in source_by_id or identifier in result_by_id:
            reasons.append(f"unexpected or duplicate id: {identifier!r}")
            continue
        result_by_id[identifier] = result

    good_source: list[dict[str, object]] = []
    good_results: list[dict[str, object]] = []
    layout: list[str] = []
    for original in source:
        identifier = str(original["id"])
        result = result_by_id.get(identifier)
        if result is None:
            reasons.append(f"missing id: {identifier}")
            continue
        placeholder_failures = restore_controls([result], {identifier: control_maps[identifier]})
        if placeholder_failures:
            reasons.append(f"placeholder changed: {identifier}")
            continue
        fatal, item_layout = validate([original], [result])
        if fatal:
            reasons.extend(fatal)
            continue
        unsafe.discard(identifier)
        good_source.append(original)
        good_results.append(result)
        layout.extend(item_layout)
    return good_results, [source_by_id[item] for item in source_by_id if item in unsafe], layout


def parse_envelope(output: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    positions = [match.start() for match in re.finditer(r"\{", output)]
    envelope = None
    for position in positions:
        try:
            candidate, _ = decoder.raw_decode(output[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "response" in candidate:
            envelope = candidate
            break
    if envelope is None:
        raise ValueError("Antigravity output did not contain a JSON response envelope")
    response = envelope.get("structured_output")
    if response is None:
        response = envelope.get("response")
    if isinstance(response, str):
        response = json.loads(response)
    if isinstance(response, dict):
        response = response.get("items")
    if not isinstance(response, list):
        raise ValueError("structured response is not an array")
    return response


def validate(
    source: list[dict[str, object]], translated: list[dict[str, object]]
) -> tuple[list[str], list[str]]:
    fatal: list[str] = []
    layout: list[str] = []
    if len(source) != len(translated):
        return [f"record count {len(translated)} != {len(source)}"], layout
    for original, result in zip(source, translated):
        identifier = str(original["id"])
        if result.get("id") != identifier:
            fatal.append(f"{identifier}: returned id {result.get('id')!r}")
            continue
        text = result.get("translation")
        if not isinstance(text, str):
            fatal.append(f"{identifier}: translation is not a string")
            continue
        template = str(original["source_template"])
        expected_newlines = template.count("@n")
        actual_newlines = text.count("@n")
        if actual_newlines < expected_newlines:
            # @n is an engine page/line terminator, not linguistic content.  If
            # the model drops it, restoring the missing copies at the end is
            # deterministic and safer than retranslating an otherwise valid
            # batch.  Extra markers remain fatal because their intended
            # removal position cannot be inferred safely.
            text += "@n" * (expected_newlines - actual_newlines)
            result["translation"] = text
        elif actual_newlines > expected_newlines:
            fatal.append(f"{identifier}: extra @n marker")
        if RAW_TOKEN.findall(template) != RAW_TOKEN.findall(text):
            fatal.append(f"{identifier}: RAW controls changed")
        if template.count("^") != text.count("^"):
            layout.append(identifier)
    return fatal, layout


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def quota_reset_seconds(output: str) -> int | None:
    """Return the advertised quota-reset delay, if Antigravity reported one."""
    match = QUOTA_RESET.search(output)
    if match is None:
        return None
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def wait_for_quota(seconds: int) -> None:
    """Wait without a long blocking sleep, then let the same attempt retry."""
    remaining = seconds + 15
    print(f"  quota exhausted; resuming automatically in {remaining}s", flush=True)
    while remaining > 0:
        interval = min(60, remaining)
        time.sleep(interval)
        remaining -= interval
        if remaining and (remaining <= 300 or remaining % 600 == 0):
            print(f"  quota wait remaining={remaining}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--glossary", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--agy", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-pro-high")
    parser.add_argument("--max-chars", type=int, default=30000)
    parser.add_argument("--max-records", type=int, default=600)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()

    records = load_jsonl(args.source)
    checkpoints = list(args.output_dir.glob("*.json"))
    checkpoints.extend(args.output_dir.parent.glob("gemini_*.json"))
    completed = load_completed(checkpoints)
    pending = [record for record in records if record["id"] not in completed]
    if args.limit is not None:
        pending = pending[: args.limit]

    rules = args.rules.read_text(encoding="utf-8")
    glossary = json.loads(args.glossary.read_text(encoding="utf-8"))
    batches = list(make_batches(pending, args.max_chars, args.max_records))
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard index must be within shard count")
    batches = [
        batch for index, batch in enumerate(batches)
        if index % args.shard_count == args.shard_index
    ]
    print(
        f"source={len(records)} completed={len(completed)} pending={len(pending)} "
        f"batches={len(batches)} model={args.model}",
        flush=True,
    )

    for ordinal, batch in enumerate(batches, 1):
        first_id = str(batch[0]["id"])
        last_id = str(batch[-1]["id"])
        safe_first = re.sub(r"[^0-9A-Za-z_-]+", "_", first_id).strip("_")[-60:]
        output = args.output_dir / f"auto_{safe_first}_{len(batch):04d}.json"
        print(
            f"[{ordinal}/{len(batches)}] {first_id} .. {last_id} "
            f"records={len(batch)} chars={sum(len(str(x['source_template'])) for x in batch)}",
            flush=True,
        )
        error = ""
        attempt = 1
        while attempt <= args.retries:
            try:
                prompt, control_maps = prompt_for(rules, glossary, batch)
                command = [
                    str(args.agy),
                    "--sandbox",
                    "--mode",
                    "plan",
                    "--model",
                    args.model,
                    "--output-format",
                    "json",
                    "--json-schema",
                    str(args.schema),
                    "--print-timeout",
                    f"{args.timeout}s",
                    "--print",
                    prompt,
                ]
                process = subprocess.run(
                    command,
                    cwd=Path.cwd(),
                    text=True,
                    capture_output=True,
                    timeout=args.timeout + 60,
                    check=False,
                )
                diagnostic = process.stdout + "\n" + process.stderr
                quota_delay = quota_reset_seconds(diagnostic)
                if quota_delay is not None:
                    wait_for_quota(quota_delay)
                    continue
                translated = parse_envelope(diagnostic)
                good, unsafe_batch, layout = partition_response(
                    batch, translated, control_maps
                )
                if unsafe_batch:
                    if good:
                        partial = output.with_name(
                            output.stem + f".partial{attempt}.json"
                        )
                        atomic_json(partial, good)
                        if layout:
                            atomic_json(partial.with_suffix(".layout.json"), layout)
                        print(
                            f"  saved partial={len(good)}; retrying unsafe={len(unsafe_batch)}",
                            flush=True,
                        )
                        batch = unsafe_batch
                        attempt += 1
                        continue
                    raise ValueError("no independently valid records returned")
                atomic_json(output, good)
                if layout:
                    atomic_json(output.with_suffix(".layout.json"), layout)
                print(
                    f"  saved {output.name}; layout_pending={len(layout)}",
                    flush=True,
                )
                break
            except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
                error = str(exc)
                print(f"  attempt {attempt}/{args.retries} failed: {error}", flush=True)
                if "process" in locals():
                    diagnostic = (process.stdout + "\n" + process.stderr).strip()
                    if diagnostic:
                        print(f"  diagnostic tail: {diagnostic[-1500:]}", flush=True)
                if attempt < args.retries:
                    time.sleep(min(5 * attempt, 15))
                attempt += 1
        else:
            print(f"FATAL batch {first_id}: {error}", file=sys.stderr, flush=True)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
