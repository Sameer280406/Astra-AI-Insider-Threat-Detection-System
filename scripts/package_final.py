import os
import sys
import zipfile
from pathlib import Path

def create_final_archive():
    src_dir = Path(__file__).resolve().parent.parent
    parent_dir = src_dir.parent
    out_zip = parent_dir / "Astra_FINAL.zip"

    excludes = {
        'node_modules', '.next', '.venv', 'venv', '__pycache__',
        '.pytest_cache', '.git', '.wrangler', '.sites-runtime',
        '.vinext', '.openai'
    }
    ex_exts = {'.pyc', '.pyo'}

    print(f"Creating archive from {src_dir} -> {out_zip}...")
    count = 0
    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in excludes]
            for f in files:
                if any(f.endswith(ext) for ext in ex_exts):
                    continue
                if f.endswith('.zip'):
                    continue
                p = Path(root) / f
                arcname = os.path.relpath(p, src_dir)
                if arcname.lower() == 'architecture.md':
                    arcname = 'ARCHITECTURE.md'
                zf.write(p, arcname)
                count += 1

    size_mb = out_zip.stat().st_size / (1024 * 1024)
    print(f"Archive created with {count} files. Size: {size_mb:.2f} MB")

    # Integrity verification
    with zipfile.ZipFile(out_zip, 'r') as zf:
        bad = zf.testzip()
        if bad:
            print(f"FAILED: Corrupted file found in archive: {bad}")
            sys.exit(1)
        print("Archive integrity check: PASSED (No corrupt files)")

        names = set(zf.namelist())
        required_docs = [
            'README.md',
            'ARCHITECTURE.md',
            'DATA_PIPELINE.md',
            'ML_TRAINING_EVALUATION.md',
            'INTEGRATIONS.md',
            'CHECKPOINT.md',
            'ml_training/cert_pipeline_report.json',
            'docker-compose.streaming.yml',
            'services/spark_streaming.py'
        ]
        all_present = True
        for req in required_docs:
            present = req in names
            print(f"  [{'PASS' if present else 'FAIL'}] Document: {req}")
            if not present:
                all_present = False

        if not all_present:
            print("ERROR: One or more required deliverables are missing from the archive!")
            sys.exit(1)

        print("\nAll deliverables verified inside Astra_FINAL.zip!")

if __name__ == '__main__':
    create_final_archive()
