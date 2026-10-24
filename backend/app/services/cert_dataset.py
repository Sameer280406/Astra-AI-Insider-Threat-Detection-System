import csv
import heapq
from datetime import datetime
from pathlib import Path
from typing import Iterator


SOURCE_FILES = ("logon.csv", "device.csv", "http.csv", "file.csv", "email.csv")


def resolve_dataset(data_root: Path) -> tuple[Path, str]:
    """Prefer a full local r4.2 release over the small reference fixture."""
    candidates = (
        (data_root / "r4.2", "cert_r4.2"),
        (data_root / "cert_reference" / "r4.2", "cert_r4.2"),
        (data_root / "cert", "cert_local"),
        (data_root / "cert_reference", "cert_reference"),
    )
    for path, provenance in candidates:
        if path.is_dir() and all((path / name).is_file() for name in SOURCE_FILES[:4]):
            return path, provenance
    searched = ", ".join(str(path) for path, _ in candidates)
    raise FileNotFoundError(f"No CERT dataset with logon/device/http/file CSVs found; searched: {searched}")


def resolve_insider_labels(data_root: Path, dataset_dir: Path) -> Path | None:
    candidates = (
        dataset_dir / "insiders.csv",
        dataset_dir.parent / "answers" / "insiders.csv",
        data_root / "answers" / "insiders.csv",
        data_root / "cert_reference" / "answers" / "insiders.csv",
    )
    return next((path for path in candidates if path.is_file()), None)


def _parse_timestamp(raw: str, source: str, row_id: str) -> datetime:
    value = raw.strip()
    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S.%f",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    raise ValueError(f"Invalid CERT timestamp in {source} event {row_id}: {value!r}")


def _iter_source(path: Path, source: str, provenance: str) -> Iterator[dict]:
    previous: datetime | None = None
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"id", "date", "user", "pc"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} is missing required fields: {sorted(missing)}")
        for row in reader:
            raw_id = str(row.get("id") or "").strip()
            user = str(row.get("user") or "").strip()
            if not raw_id or not user:
                raise ValueError(f"{path.name} contains an event without an id or user")
            timestamp = _parse_timestamp(str(row.get("date") or ""), path.name, raw_id)
            if previous is not None and timestamp < previous:
                raise ValueError(
                    f"{path.name} is not ordered by event time at source event {raw_id}; "
                    "chronological replay cannot safely merge this file"
                )
            previous = timestamp

            event = {
                "eventId": f"{source.upper()}:{raw_id}",
                "timestamp": timestamp,
                "user": user,
                "pc": str(row.get("pc") or ""),
                "type": source,
                "activity": str(row.get("activity") or source),
                "filename": str(row.get("filename") or row.get("url") or ""),
                "provenance": provenance,
                "isSynthetic": False,
            }
            if source == "email":
                raw_size = str(row.get("size") or "").strip()
                if raw_size:
                    try:
                        event["bytes"] = int(raw_size)
                    except ValueError as exc:
                        raise ValueError(f"Invalid email size in event {raw_id}") from exc
            elif source == "http":
                content = str(row.get("content") or "")
                marker = "bytes="
                if marker in content:
                    try:
                        event["bytes"] = int(content.split(marker, 1)[1].split(";", 1)[0].strip())
                    except ValueError as exc:
                        raise ValueError(f"Invalid HTTP byte count in event {raw_id}") from exc
            event["bytesKnown"] = "bytes" in event
            yield event


def iter_cert_events(dataset_dir: Path, provenance: str) -> Iterator[dict]:
    """Merge chronologically ordered CERT channel CSVs with bounded memory."""
    sources = [
        _iter_source(dataset_dir / name, name.removesuffix(".csv"), provenance)
        for name in SOURCE_FILES
        if (dataset_dir / name).is_file()
    ]
    yield from heapq.merge(*sources, key=lambda event: event["timestamp"])
