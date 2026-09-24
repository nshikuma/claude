#!/usr/bin/env python3
"""Regenerate docs/demo/ (example results shown on the website) from the
synthetic test genome.

    python3 tests/make_demo.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import make_test_data  # noqa: E402
from test_pipeline import issue_body  # noqa: E402


def main():
    tmp = tempfile.mkdtemp()
    demo = os.path.join(ROOT, "docs", "demo")
    try:
        make_test_data.main(os.path.join(tmp, "data"))
        db = os.path.join(tmp, "db")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_db.py"), "--genome",
                        os.path.join(tmp, "data", "genome.fa"), "--gff", os.path.join(tmp, "data", "annotation.gff3"),
                        "--out", db, "--assembly", "synthetic test genome"], check=True, capture_output=True)
        mrna = subprocess.run(["blastdbcmd", "-db", os.path.join(db, "transcripts"), "-entry", "HELE_000101-RA",
                               "-outfmt", "%s"], capture_output=True, text=True, check=True).stdout.strip()
        jobs = {
            "tblastn": issue_body("tblastn", "genome", ">histone_H3 Histone H3.3, human\n%s\n>ubiquitin Ubiquitin, human\n%s"
                                  % (make_test_data.H3, make_test_data.UBQ), **{"Job title": "Histone H3 and ubiquitin vs genome"}),
            "blastn": issue_body("blastn", "genome", ">H3_mRNA\n" + mrna, **{"blastn task": "dc-megablast"}),
            "blastp": issue_body("blastp", "proteins", ">histone_H3 Histone H3.3, human\n" + make_test_data.H3),
        }
        if os.path.isdir(demo):
            shutil.rmtree(demo)
        os.makedirs(demo)
        for n, (name, body) in enumerate(jobs.items(), 1):
            out = os.path.join(tmp, "out_" + name)
            env = dict(os.environ, ISSUE_BODY=body, ISSUE_NUMBER=str(n), ISSUE_AUTHOR="demo-user",
                       DB_DIR=db, OUT_DIR=out)
            subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run_blast.py")], env=env, check=True,
                           capture_output=True)
            shutil.copytree(os.path.join(out, "results", str(n)), os.path.join(demo, name))
        with open(os.path.join(db, "info.json")) as fh:
            info = json.load(fh)
        info["species"] = "Hydroides elegans"
        with open(os.path.join(demo, "info.json"), "w") as fh:
            json.dump(info, fh, indent=1)
        print("Wrote", demo)
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
