#!/usr/bin/env python3
"""End-to-end test: synthetic genome -> BLAST databases -> every program.

    python3 tests/test_pipeline.py

Needs BLAST+ and gffread (sudo apt-get install ncbi-blast+ gffread).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)

import labcrypt  # noqa: E402
import make_test_data  # noqa: E402
import run_blast  # noqa: E402

H3_PEP = make_test_data.H3


def issue_body(program, database, fasta, **extra):
    """Same layout the website and the issue form produce."""
    parts = ["### Program", program, "### Database", database]
    for k, v in extra.items():
        parts += ["### " + k, str(v)]
    parts += ["### Query sequences", "```fasta", fasta, "```"]
    return "\n\n".join(parts)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        make_test_data.main(os.path.join(cls.tmp, "data"))
        cls.db = os.path.join(cls.tmp, "db")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_db.py"),
                        "--genome", os.path.join(cls.tmp, "data", "genome.fa"),
                        "--gff", os.path.join(cls.tmp, "data", "annotation.gff3"),
                        "--out", cls.db], check=True, capture_output=True)
        with open(os.path.join(cls.db, "info.json")) as fh:
            cls.info = json.load(fh)
        # A nucleotide query: the H3 transcript, taken from the transcripts db.
        cls.h3_mrna = subprocess.run(["blastdbcmd", "-db", os.path.join(cls.db, "transcripts"),
                                      "-entry", "HELE_000101-RA", "-outfmt", "%s"],
                                     capture_output=True, text=True, check=True).stdout.strip()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def run_job(self, body, lab_key=None):
        Pipeline.n += 1
        out = os.path.join(self.tmp, "out%d" % self.n)
        env = dict(os.environ, ISSUE_BODY=body, ISSUE_NUMBER=str(self.n), ISSUE_AUTHOR="tester",
                   DB_DIR=self.db, OUT_DIR=out, SITE_URL="https://example.github.io/blast")
        env.pop("LAB_KEY", None)
        if lab_key:
            env["LAB_KEY"] = lab_key
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run_blast.py")], env=env, check=True,
                              capture_output=True, text=True)
        res_dir = os.path.join(out, "results", str(self.n))
        if lab_key:
            files = os.listdir(res_dir)
            self.assertTrue(files and all(f.endswith(".enc") for f in files), files)
            with open(os.path.join(res_dir, "result.json.enc")) as fh:
                res = json.loads(labcrypt.decrypt(fh.read(), lab_key))
            res["_log"] = proc.stdout + proc.stderr
            res["_scrub"] = os.path.exists(os.path.join(out, "scrub"))
        else:
            with open(os.path.join(res_dir, "result.json")) as fh:
                res = json.load(fh)
        with open(os.path.join(out, "comment.md")) as fh:
            res["_comment"] = fh.read()
        return res

    def top(self, res):
        self.assertEqual(res["status"], "done", res.get("error"))
        return res["queries"][0]["hits"][0]

    def test_blastp(self):
        hit = self.top(self.run_job(issue_body("blastp", "proteins", ">h3\n" + H3_PEP)))
        self.assertEqual(hit["id"], "HELE_000101-RA")
        self.assertEqual(hit["gene"]["name"], "H3")
        self.assertEqual(hit["identity"], 100.0)

    def test_tblastn_genome_finds_both_exons_and_gene(self):
        hit = self.top(self.run_job(issue_body("tblastn", "genome", ">h3\n" + H3_PEP)))
        self.assertEqual(hit["id"], "scaffold_1")
        self.assertGreaterEqual(hit["hsp_count"], 3)  # two exons + the unannotated copy
        genes = [p["genes"] for p in hit["hsps"]]
        in_gene = [g for g in genes if g["overlap"]]
        self.assertEqual(len(in_gene), 2)  # one per exon, split by the intron
        self.assertTrue(all(g["overlap"][0]["id"] == "HELE_000101" for g in in_gene))
        intergenic = [g for g in genes if not g["overlap"]]
        self.assertEqual(intergenic[0]["nearest"]["id"], "HELE_000101")
        self.assertGreater(intergenic[0]["nearest"]["distance"], 20000)
        self.assertGreater(hit["query_cover"], 90)

    def test_tblastn_transcripts(self):
        hit = self.top(self.run_job(issue_body("tblastn", "transcripts", ">ubq\n" + make_test_data.UBQ)))
        self.assertEqual(hit["id"], "HELE_000202-RA")

    def test_blastn_all_tasks(self):
        for task in ("megablast", "dc-megablast", "blastn"):
            hit = self.top(self.run_job(issue_body("blastn", "genome", ">mrna\n" + self.h3_mrna, **{"blastn task": task})))
            self.assertEqual(hit["id"], "scaffold_1", task)

    def test_blastn_short_query_switches_task(self):
        res = self.run_job(issue_body("blastn", "transcripts", self.h3_mrna[100:125]))
        self.assertEqual(res["request"]["task"], "blastn-short")
        self.assertEqual(self.top(res)["id"], "HELE_000101-RA")

    def test_blastx(self):
        hit = self.top(self.run_job(issue_body("blastx", "proteins", ">mrna\n" + self.h3_mrna)))
        self.assertEqual(hit["id"], "HELE_000101-RA")

    def test_tblastx(self):
        hit = self.top(self.run_job(issue_body("tblastx", "genome", ">mrna\n" + self.h3_mrna)))
        self.assertEqual(hit["id"], "scaffold_1")

    def test_multiple_queries_and_no_hits(self):
        fasta = ">h3\n%s\n>ubq\n%s\n>junk\nWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW" % (H3_PEP, make_test_data.UBQ)
        res = self.run_job(issue_body("blastp", "proteins", fasta, **{"E-value": "1e-5"}))
        self.assertEqual([q["id"] for q in res["queries"]], ["h3", "ubq", "junk"])
        self.assertEqual(res["queries"][2]["hits"], [])
        self.assertIn("Open the full results", res["_comment"])

    def test_invalid_requests_fail_politely(self):
        cases = [
            (issue_body("blastp", "genome", H3_PEP), "cannot search"),
            (issue_body("blastn", "genome", H3_PEP), "needs a nucleotide query"),
            (issue_body("rm -rf", "genome", "ACGT"), "Unknown BLAST program"),
            (issue_body("blastn", "genome", "ACGT$(whoami)"), "not sequence letters"),
            (issue_body("blastp", "proteins", H3_PEP, **{"E-value": "lots"}), "not a number"),
            (issue_body("blastp", "proteins", ""), "No query"),
        ]
        for body, msg in cases:
            res = self.run_job(body)
            self.assertEqual(res["status"], "failed")
            self.assertIn(msg, res["error"])
            self.assertIn("could not run", res["_comment"])

    def test_private_mode(self):
        key = "correct horse battery staple"
        wrapped = lambda body, k=key: "Submitted from the website.\n\n%s\n%s\n%s\n" % (
            labcrypt.MARKER, labcrypt.encrypt(body, k), labcrypt.END_MARKER)
        res = self.run_job(wrapped(issue_body("blastp", "proteins", ">secret_query\n" + H3_PEP)), lab_key=key)
        self.assertEqual(self.top(res)["id"], "HELE_000101-RA")
        self.assertNotIn("HELE", res["_comment"])          # the public comment says nothing
        self.assertNotIn("secret_query", res["_log"])      # nor does the public log
        self.assertNotIn("HELE", res["_log"])
        # plain sequences on a private server: refused and flagged for removal
        res = self.run_job(issue_body("blastp", "proteins", H3_PEP), lab_key=key)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(res["_scrub"])
        self.assertIn("text of this issue has been removed", res["_comment"])
        # old passphrase
        res = self.run_job(wrapped(issue_body("blastp", "proteins", H3_PEP), "old passphrase"), lab_key=key)
        self.assertIn("could not be decrypted", res["error"])
        # encrypted request sent to a server that isn't private
        res = self.run_job(wrapped(issue_body("blastp", "proteins", H3_PEP)))
        self.assertIn("not in private mode", res["error"])

    def test_info(self):
        self.assertEqual(self.info["databases"]["genome"]["sequences"], 3)
        self.assertEqual(self.info["databases"]["proteins"]["sequences"], 3)
        self.assertTrue(self.info["examples"]["protein"]["seq"])


class Crypto(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        env = labcrypt.encrypt("ACGT" * 1000, "pw")
        self.assertEqual(labcrypt.decrypt(env, "pw"), b"ACGT" * 1000)
        with self.assertRaises(labcrypt.DecryptError):
            labcrypt.decrypt(env, "nope")

    def test_envelope_survives_line_wrapping(self):
        env = labcrypt.encrypt("hello", "pw")
        body = "x\n%s\n%s\n%s\n" % (labcrypt.MARKER, "\n".join(env[i:i + 60] for i in range(0, len(env), 60)), labcrypt.END_MARKER)
        self.assertEqual(labcrypt.decrypt(labcrypt.find_envelope(body), "pw"), b"hello")


class Parsing(unittest.TestCase):
    def test_issue_form_output(self):
        body = ("### Program\n\nblastp — protein vs protein\n\n### Database\n\nPredicted proteins\n\n"
                "### E-value\n\n_No response_\n\n### Query sequences\n\n```fasta\n>a b c\nMKV LLA\n12 GG\n```\n")
        o = run_blast.parse_request(body)
        self.assertEqual((o["program"], o["database"], o["evalue"]), ("blastp", "proteins", 0.001))
        self.assertEqual(o["queries"], [("a b c", "MKVLLAGG")])

    def test_header_sanitised(self):
        o = run_blast.parse_request(issue_body("blastp", "proteins", ">x`$(id)<script>\nMKV"))
        self.assertNotRegex(o["queries"][0][0], r"[`$<>]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
