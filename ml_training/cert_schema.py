"""
CERT Insider Threat Dataset Schema Definitions and Ingestion Utilities
CMU SEI CERT r4.2 / r5.2 / r6.2 format specifications.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import pandas as pd
import numpy as np

# Official CERT file schemas
CERT_SCHEMAS = {
    "logon.csv": ["id", "date", "user", "pc", "activity"],
    "device.csv": ["id", "date", "user", "pc", "activity"],
    "http.csv": ["id", "date", "user", "pc", "url", "content"],
    "file.csv": ["id", "date", "user", "pc", "filename", "content"],
    "email.csv": ["id", "date", "user", "pc", "to", "cc", "bcc", "from", "size", "attachments", "content"],
    "insiders.csv": ["scenario", "details", "user", "start", "end"],
}

# Standard CERT activities
LOGON_ACTIVITIES = {"Logon", "Logoff"}
DEVICE_ACTIVITIES = {"Connect", "Disconnect"}

@dataclass
class DatasetValidationReport:
    is_valid: bool
    record_counts: Dict[str, Optional[int]] = field(default_factory=dict)
    record_counts_complete: bool = False
    missing_values: Dict[str, int] = field(default_factory=dict)
    users_count: int = 0
    users_count_complete: bool = False
    date_range: Tuple[Optional[datetime], Optional[datetime]] = (None, None)
    anomalous_records: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

def parse_cert_timestamp(date_series: pd.Series) -> pd.Series:
    """Robustly parse timestamps from CERT format (MM/DD/YYYY HH:MM:SS or ISO)."""
    return pd.to_datetime(date_series, format="mixed", errors="coerce")

def validate_cert_directory(cert_dir: Path) -> DatasetValidationReport:
    """Inspects and validates raw CERT dataset files in the target directory."""
    report = DatasetValidationReport(is_valid=True)
    if not cert_dir.exists():
        report.is_valid = False
        report.errors.append(f"Directory {cert_dir} does not exist.")
        return report

    all_users = set()
    min_date = None
    max_date = None

    for filename, expected_cols in CERT_SCHEMAS.items():
        file_path = cert_dir / filename
        if not file_path.exists():
            report.warnings.append(f"Optional file {filename} not found.")
            continue

        try:
            sample_df = pd.read_csv(file_path, nrows=500)
            missing_cols = set(expected_cols) - set(sample_df.columns)
            if missing_cols:
                report.is_valid = False
                report.errors.append(f"{filename} is missing expected columns: {missing_cols}")

            report.record_counts[filename] = None
            if "user" in sample_df.columns:
                all_users.update(sample_df["user"].dropna().unique())
            if "date" in sample_df.columns:
                dates = parse_cert_timestamp(sample_df["date"])
                valid_dates = dates.dropna()
                if not valid_dates.empty:
                    f_min, f_max = valid_dates.min(), valid_dates.max()
                    min_date = min_date or f_min
                    max_date = max_date or f_max
                    min_date = min(min_date, f_min)
                    max_date = max(max_date, f_max)
        except Exception as e:
            report.is_valid = False
            report.errors.append(f"Error reading {filename}: {str(e)}")

    report.users_count = len(all_users)
    report.date_range = (min_date, max_date)
    return report

def generate_cert_reference_subset(output_dir: Path, n_users: int = 15, n_days: int = 30, seed: int = 42) -> Path:
    """
    Generates a deterministic reference dataset strictly adhering to CERT r4.2 schema.
    Used for local pipeline validation, testing, and continuous verification when raw CERT archives
    are not yet downloaded on the host machine.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.RandomState(seed)

    users = [f"USR{i:04d}" for i in range(1, n_users + 1)]
    departments = ["Engineering", "Finance", "HR", "Sales", "Operations"]
    user_dept = {u: departments[i % len(departments)] for i, u in enumerate(users)}
    pcs = [f"PC-{i:04d}" for i in range(1, n_users + 5)]
    user_primary_pc = {u: pcs[i] for i, u in enumerate(users)}

    start_date = datetime(2026, 1, 1, 8, 0, 0)
    
    logons, devices, https, files, emails = [], [], [], [], []
    event_id = 1

    # Designate 3 malicious users with distinct incident windows across Train, Val, and Test
    insider_1 = users[2]   # Scenario 1: Data exfiltration via USB (Train window)
    insider_1_start = datetime(2026, 1, 12, 18, 0, 0)
    insider_1_end = datetime(2026, 1, 14, 23, 0, 0)

    insider_2 = users[6]   # Scenario 2: Off-hours credential misuse & sensitive access (Val window)
    insider_2_start = datetime(2026, 1, 26, 21, 0, 0)
    insider_2_end = datetime(2026, 1, 28, 4, 0, 0)

    insider_3 = users[10]  # Scenario 3: Disgruntled bulk download & abnormal drift (Test window)
    insider_3_start = datetime(2026, 1, 31, 19, 0, 0)
    insider_3_end = datetime(2026, 2, 2, 23, 0, 0)

    for day in range(n_days):
        current_day = start_date + pd.Timedelta(days=day)
        is_weekend = current_day.weekday() >= 5

        for u in users:
            pc = user_primary_pc[u]
            if is_weekend and u not in (insider_1, insider_2, insider_3):
                if rng.rand() > 0.05:
                    continue

            # Check if user is currently in an active incident window
            is_ins_1 = (u == insider_1) and (insider_1_start <= current_day <= insider_1_end)
            is_ins_2 = (u == insider_2) and (insider_2_start <= current_day <= insider_2_end)
            is_ins_3 = (u == insider_3) and (insider_3_start <= current_day <= insider_3_end)
            is_active_insider = is_ins_1 or is_ins_2 or is_ins_3

            # Logon
            login_hour = 8 + rng.randint(0, 2)
            if is_ins_2:
                login_hour = 22 + rng.randint(0, 3)  # Off-hours
                pc = pcs[-1]  # New unknown device

            login_time = current_day.replace(hour=login_hour % 24, minute=rng.randint(0, 59))
            logons.append({"id": f"LOG-{event_id}", "date": login_time.strftime("%m/%d/%Y %H:%M:%S"), "user": u, "pc": pc, "activity": "Logon"})
            event_id += 1

            # Activity count
            n_events = rng.randint(5, 25)
            if is_active_insider:
                n_events = rng.randint(40, 80)

            for _ in range(n_events):
                ev_time = login_time + pd.Timedelta(minutes=rng.randint(5, 480))
                
                # HTTP
                bytes_transferred = int(rng.exponential(scale=50_000))
                url = "http://internal-wiki.company.local/docs"
                if (is_ins_1 or is_ins_3) and rng.rand() < 0.35:
                    bytes_transferred = int(rng.uniform(5_000_000, 45_000_000))  # Huge egress
                    url = "https://external-cloud-dropbox.com/upload"

                https.append({
                    "id": f"HTTP-{event_id}", "date": ev_time.strftime("%m/%d/%Y %H:%M:%S"),
                    "user": u, "pc": pc, "url": url,
                    "content": f"bytes={bytes_transferred}"
                })
                event_id += 1

                # File activity
                filename = "meeting_notes.txt"
                if is_active_insider and rng.rand() < 0.4:
                    filename = rng.choice(["passwords_export.kdbx", "quarterly_financials_restricted.xlsx", "source_code_archive.zip"])
                
                files.append({
                    "id": f"FILE-{event_id}", "date": ev_time.strftime("%m/%d/%Y %H:%M:%S"),
                    "user": u, "pc": pc, "filename": filename,
                    "content": "READ,COPY" if filename.endswith((".kdbx", ".zip")) else "READ"
                })
                event_id += 1

                # Device activity (USB)
                if (is_ins_1 or is_ins_3) and rng.rand() < 0.25:
                    devices.append({
                        "id": f"DEV-{event_id}", "date": ev_time.strftime("%m/%d/%Y %H:%M:%S"),
                        "user": u, "pc": pc, "activity": "Connect"
                    })
                    event_id += 1

            # Logoff
            logoff_time = login_time + pd.Timedelta(hours=8, minutes=rng.randint(10, 50))
            logons.append({"id": f"LOG-{event_id}", "date": logoff_time.strftime("%m/%d/%Y %H:%M:%S"), "user": u, "pc": pc, "activity": "Logoff"})
            event_id += 1

    def write_ordered(name: str, rows: List[Dict], columns: List[str]) -> None:
        frame = pd.DataFrame(rows, columns=columns)
        if not frame.empty:
            frame["_sort_timestamp"] = pd.to_datetime(frame["date"], format="mixed")
            frame = frame.sort_values("_sort_timestamp", kind="stable").drop(columns="_sort_timestamp")
        frame.to_csv(output_dir / name, index=False)

    write_ordered("logon.csv", logons, CERT_SCHEMAS["logon.csv"])
    write_ordered("device.csv", devices, CERT_SCHEMAS["device.csv"])
    write_ordered("http.csv", https, CERT_SCHEMAS["http.csv"])
    write_ordered("file.csv", files, CERT_SCHEMAS["file.csv"])
    email_rows = emails or [{
        "id": "EM-1", "date": start_date.strftime("%m/%d/%Y %H:%M:%S"),
        "user": users[0], "pc": pcs[0], "to": "a@c.com", "cc": "",
        "bcc": "", "from": "b@c.com", "size": 1024,
        "attachments": "", "content": "hello",
    }]
    write_ordered("email.csv", email_rows, CERT_SCHEMAS["email.csv"])

    insiders_df = pd.DataFrame([
        {"scenario": "1", "details": "Data exfiltration via USB and high HTTP egress", "user": insider_1, "start": insider_1_start.strftime("%m/%d/%Y %H:%M:%S"), "end": insider_1_end.strftime("%m/%d/%Y %H:%M:%S")},
        {"scenario": "2", "details": "Off-hours logon, new device, restricted credential access", "user": insider_2, "start": insider_2_start.strftime("%m/%d/%Y %H:%M:%S"), "end": insider_2_end.strftime("%m/%d/%Y %H:%M:%S")},
        {"scenario": "3", "details": "Disgruntled bulk exfiltration and off-hours spike", "user": insider_3, "start": insider_3_start.strftime("%m/%d/%Y %H:%M:%S"), "end": insider_3_end.strftime("%m/%d/%Y %H:%M:%S")},
    ])
    insiders_df.to_csv(output_dir / "insiders.csv", index=False)
    (output_dir / "_synthetic_manifest.json").write_text(
        json.dumps({"kind": "generated_cert_reference_fixture", "seed": seed}),
        encoding="utf-8",
    )

    return output_dir
