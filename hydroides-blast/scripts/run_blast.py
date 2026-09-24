#!/usr/bin/env python3
"""Run one BLAST search described by a GitHub issue and write the results.

Reads the request from environment variables (never from the command line, so
nothing a user types is ever interpreted by a shell):

    ISSUE_BODY, ISSUE_TITLE, ISSUE_NUMBER, ISSUE_AUTHOR   the request
    DB_DIR        directory made by build_db.py
    OUT_DIR       where to write results/<issue>/... and comment.md
    SITE_URL      website base URL, used for the link in the comment
    BLAST_THREADS optional, default = CPU count
    LAB_KEY       optional lab passphrase. When set ("private mode") the request
                  must be encrypted by the website, and every result file is
                  written encrypted (*.enc), so nothing readable is published.

Exit status is 0 whether the search succeeded or the request was invalid; the
outcome is in OUT_DIR/status (``done`` or ``failed``).
"""

import bisect
import glob
import json
import os
import re
import subprocess
import sys
import time

# program -> (query type, allowed databases)
PROGRAMS = {
    "blastn": ("nucleotide", ("genome", "transcripts")),
    "tblastx": ("nucleotide", ("genome", "transcripts")),
    "blastx": ("nucleotide", ("proteins",)),
    "tblastn": ("protein", ("genome", "transcripts")),
    "blastp": ("protein", ("proteins",)),
}
DB_LABEL = {"genome": "Genome assembly", "transcripts": "Transcripts (mRNA)", "proteins": "Predicted proteins"}
TASKS = {"megablast", "dc-megablast", "blastn", "blastn-short"}
MATRICES = {"BLOSUM62", "BLOSUM45", "BLOSUM50", "BLOSUM80", "BLOSUM90", "PAM30", "PAM70", "PAM250"}

MAX_SEQS = 25
# Total query residues allowed per program. tblastx translates both the query
# and a whole genome in six frames, so it gets a much smaller budget.
MAX_TOTAL = {"blastn": 200000, "blastx": 50000, "tblastx": 10000, "tblastn": 20000, "blastp": 50000}
MAX_HSPS_IN_JSON = 50

NUC_CHARS = set("ACGTUNRYKMSWBDHV-")
PROT_CHARS = set("ABCDEFGHIKLMNPQRSTUVWXYZ*-")


class RequestError(Exception):
    pass


# --- parsing the issue -------------------------------------------------------

def parse_sections(body):
    """Split an issue body into {normalised heading: text} using '### Heading'
    lines, the layout GitHub issue forms produce and the website imitates."""
    sections, current, buf = {}, None, []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^#{2,4}\s+(.+?)\s*$", line)
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = re.sub(r"[^a-z]", "", m.group(1).lower()), []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def value(sections, *names):
    for n in names:
        v = sections.get(n, "").strip()
        if v and v != "_No response_":
            return v
    return ""


def parse_fasta_text(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("```")]
    records, name, seq = [], None, []
    for line in lines:
        if line.startswith(">"):
            if name is not None or seq:
                records.append((name, "".join(seq)))
            name, seq = line[1:].strip(), []
        else:
            seq.append(re.sub(r"[\s\d]", "", line).upper())
    if name is not None or seq:
        records.append((name, "".join(seq)))
    out = []
    for i, (name, seq) in enumerate(records, 1):
        if not seq:
            raise RequestError("Sequence %s is empty." % (name or i))
        name = re.sub(r"[^\w.:|=+\-()\[\], ]", "_", name or "") or "Query_%d" % i
        out.append((name[:120], seq))
    return out


def seq_type(seq):
    letters = [c for c in seq if c != "-"]
    if not letters:
        return "empty"
    nuc = sum(c in "ACGTUN" for c in letters)
    if nuc / len(letters) >= 0.9 and set(letters) <= NUC_CHARS:
        return "nucleotide"
    if set(letters) <= PROT_CHARS:
        return "protein"
    return "invalid"


def parse_request(body):
    s = parse_sections(body)
    program = value(s, "program", "blastprogram").split()[0].lower() if value(s, "program", "blastprogram") else ""
    if program not in PROGRAMS:
        raise RequestError("Unknown BLAST program %r. Use one of: %s." % (program, ", ".join(PROGRAMS)))

    db_text = value(s, "database", "searchagainst").lower()
    database = next((d for d in ("genome", "transcripts", "proteins") if d in db_text
                     or d.rstrip("s") in db_text), None)
    if database is None:
        raise RequestError("Unknown database %r. Use genome, transcripts or proteins." % db_text)
    qtype, dbs = PROGRAMS[program]
    if database not in dbs:
        raise RequestError("%s cannot search the %s database (it needs: %s)." % (program, database, ", ".join(dbs)))

    queries = parse_fasta_text(value(s, "querysequences", "querysequence", "query", "sequence", "sequences"))
    if not queries:
        raise RequestError("No query sequence found.")
    if len(queries) > MAX_SEQS:
        raise RequestError("Too many sequences (%d). The limit is %d per search." % (len(queries), MAX_SEQS))
    for name, seq in queries:
        t = seq_type(seq)
        if t == "invalid":
            bad = sorted(set(seq) - PROT_CHARS - NUC_CHARS)
            raise RequestError("Sequence %s contains characters that are not sequence letters: %s" % (name, " ".join(bad) or "?"))
        if t != qtype:
            raise RequestError("%s needs a %s query, but %s looks like %s." % (program, qtype, name, t))
    total = sum(len(q) for _, q in queries)
    if total > MAX_TOTAL[program]:
        raise RequestError("Queries total %s residues; %s is limited to %s per search here. Split it into smaller searches."
                           % (format(total, ","), program, format(MAX_TOTAL[program], ",")))

    opts = {"program": program, "database": database, "queries": queries}
    ev = value(s, "evalue", "expectthreshold", "evaluethreshold") or "0.001"
    try:
        opts["evalue"] = float(ev)
    except ValueError:
        raise RequestError("E-value %r is not a number." % ev)
    if not 0 < opts["evalue"] <= 1000:
        raise RequestError("E-value must be between 0 and 1000.")
    mh = value(s, "maxhits", "maxtargetsequences", "maxtargetseqs") or "50"
    if not mh.isdigit() or not 1 <= int(mh) <= 500:
        raise RequestError("Max hits must be a whole number from 1 to 500.")
    opts["max_hits"] = int(mh)

    task = value(s, "blastntask", "task", "optimizefor").split()[0].lower() if value(s, "blastntask", "task", "optimizefor") else ""
    if program == "blastn":
        task = task or "megablast"
        if task not in TASKS:
            raise RequestError("Unknown blastn task %r." % task)
        if task in ("megablast", "blastn") and max(len(q) for _, q in queries) < 30:
            task = "blastn-short"
        opts["task"] = task
    matrix = value(s, "matrix", "scoringmatrix").split()[0].upper() if value(s, "matrix", "scoringmatrix") else ""
    if program != "blastn":
        matrix = matrix or "BLOSUM62"
        if matrix not in MATRICES:
            raise RequestError("Unknown scoring matrix %r." % matrix)
        opts["matrix"] = matrix
    flt = value(s, "lowcomplexityfilter", "filter").lower()
    opts["filter"] = not flt.startswith(("no", "off", "false"))
    opts["title"] = re.sub(r"[<>`]", "", value(s, "jobtitle", "title"))[:100]
    return opts


# --- annotation --------------------------------------------------------------

class Annotation:
    def __init__(self, path):
        data = {"genes": {}, "transcripts": {}}
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
        self.genes = data.get("genes", {})
        self.transcripts = data.get("transcripts", {})
        self.by_contig = {}
        for gid, (seqid, start, end, strand, name, desc) in self.genes.items():
            self.by_contig.setdefault(seqid, []).append((start, end, strand, gid, name, desc))
        self.starts, self.maxlen = {}, {}
        for seqid, rows in self.by_contig.items():
            rows.sort()
            self.starts[seqid] = [r[0] for r in rows]
            self.maxlen[seqid] = max(r[1] - r[0] for r in rows) + 1

    def gene_record(self, row):
        start, end, strand, gid, name, desc = row
        return {"id": gid, "name": name, "desc": desc, "loc": [start, end, strand]}

    def genome_region(self, seqid, a, b):
        """Genes overlapping [a, b] on seqid; if none, the nearest gene."""
        rows = self.by_contig.get(seqid)
        if not rows:
            return {"overlap": [], "nearest": None}
        starts = self.starts[seqid]
        i = bisect.bisect_right(starts, b)
        hits = []
        j = i - 1
        while j >= 0 and starts[j] >= a - self.maxlen[seqid]:
            if rows[j][1] >= a:
                hits.append(self.gene_record(rows[j]))
            j -= 1
        hits.reverse()
        if hits:
            return {"overlap": hits, "nearest": None}
        best = None
        for k in (i - 1, i):
            if 0 <= k < len(rows):
                dist = a - rows[k][1] if rows[k][1] < a else rows[k][0] - b
                if best is None or dist < best[0]:
                    best = (dist, rows[k])
        # the gene ending closest before a may not be rows[i-1]; good enough
        # for a "nearest gene" hint.
        near = self.gene_record(best[1])
        near["distance"] = best[0]
        return {"overlap": [], "nearest": near}

    def transcript(self, tid):
        t = self.transcripts.get(tid)
        if not t:
            return None
        gene, name, desc, seqid, start, end, strand = t
        return {"id": gene, "transcript": tid, "name": name, "desc": desc, "loc": [seqid, start, end, strand]}


# --- running BLAST -----------------------------------------------------------

def blast_command(opts, db_path, query_path, archive_path, threads):
    prog = opts["program"]
    cmd = [prog, "-query", query_path, "-db", db_path, "-outfmt", "11", "-out", archive_path,
           "-evalue", repr(opts["evalue"]), "-max_target_seqs", str(opts["max_hits"]),
           "-num_threads", str(threads)]
    if prog == "blastn":
        cmd += ["-task", opts["task"], "-dust", "yes" if opts["filter"] else "no"]
    else:
        cmd += ["-matrix", opts["matrix"], "-seg", "yes" if opts["filter"] else "no"]
    return cmd


def strip_lcl(s):
    return s[4:] if s.startswith("lcl|") else s


def union_length(ranges):
    total, cur = 0, None
    for a, b in sorted(ranges):
        if cur is None or a > cur[1] + 1:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


def summarise(report_json, opts, ann):
    """Turn BLAST's outfmt 15 JSON into the compact form the website reads."""
    queries = []
    for entry in report_json.get("BlastOutput2", []):
        search = entry["report"]["results"]["search"]
        q = {"id": search.get("query_title") or search.get("query_id"), "len": search["query_len"],
             "hits": [], "message": search.get("message", "")}
        for hit in search.get("hits", []):
            d = hit["description"][0]
            sid = strip_lcl(d.get("accession") or d.get("id"))
            title = d.get("title", "")
            if title.startswith(sid):
                title = title[len(sid):].strip()
            h = {"id": sid, "title": title, "len": hit["len"], "hsps": []}
            for hsp in hit["hsps"][:MAX_HSPS_IN_JSON]:
                x = {k: hsp.get(k) for k in ("bit_score", "score", "evalue", "identity", "positive", "gaps",
                                               "align_len", "query_from", "query_to", "hit_from", "hit_to",
                                               "query_frame", "hit_frame", "query_strand", "hit_strand",
                                               "qseq", "hseq", "midline")}
                x = {k: v for k, v in x.items() if v is not None}
                x["bit_score"] = round(x["bit_score"], 1)
                if opts["database"] == "genome":
                    lo, hi = sorted((hsp["hit_from"], hsp["hit_to"]))
                    x["genes"] = ann.genome_region(sid, lo, hi)
                h["hsps"].append(x)
            h["hsp_count"] = len(hit["hsps"])
            best = hit["hsps"][0]
            qranges = [tuple(sorted((p["query_from"], p["query_to"]))) for p in hit["hsps"]]
            h["max_score"] = round(max(p["bit_score"] for p in hit["hsps"]), 1)
            h["total_score"] = round(sum(p["bit_score"] for p in hit["hsps"]), 1)
            h["evalue"] = min(p["evalue"] for p in hit["hsps"])
            h["query_cover"] = round(100.0 * union_length(qranges) / q["len"], 1)
            h["identity"] = round(100.0 * best["identity"] / best["align_len"], 2)
            if opts["database"] != "genome":
                h["gene"] = ann.transcript(sid)
            q["hits"].append(h)
        queries.append(q)
    return queries


def gene_label(hit, hsp=None):
    if hit.get("gene"):
        g = hit["gene"]
        return " ".join(x for x in (g["name"] or g["id"], "— " + g["desc"] if g["desc"] else "") if x)
    if hsp is None:
        seen = {}
        for p in hit["hsps"]:
            for g in (p.get("genes") or {}).get("overlap", []):
                seen.setdefault(g["id"], g)
        if seen:
            return "; ".join((g["name"] or g["id"]) + (" (%s)" % g["desc"] if g["desc"] else "") for g in seen.values())
    genes = (hsp or hit["hsps"][0]).get("genes")
    if not genes:
        return ""
    if genes["overlap"]:
        return "; ".join((g["name"] or g["id"]) + (" (%s)" % g["desc"] if g["desc"] else "") for g in genes["overlap"])
    if genes["nearest"]:
        n = genes["nearest"]
        return "intergenic; nearest %s (%s kb)" % (n["name"] or n["id"], round(n["distance"] / 1000.0, 1))
    return ""


def write_tsv(path, queries):
    cols = ["query", "subject", "gene", "pct_identity", "align_len", "mismatches", "gaps", "q_start", "q_end",
            "s_start", "s_end", "evalue", "bitscore", "query_cover_pct"]
    with open(path, "w") as fh:
        fh.write("\t".join(cols) + "\n")
        for q in queries:
            for h in q["hits"]:
                for p in h["hsps"]:
                    row = [q["id"], h["id"], gene_label(h, p), "%.2f" % (100.0 * p["identity"] / p["align_len"]),
                           p["align_len"], p["align_len"] - p["identity"] - p.get("gaps", 0), p.get("gaps", 0),
                           p["query_from"], p["query_to"], p["hit_from"], p["hit_to"], "%.3g" % p["evalue"],
                           p["bit_score"], h["query_cover"]]
                    fh.write("\t".join(str(c).replace("\t", " ") for c in row) + "\n")


def write_hit_fasta(path, queries):
    with open(path, "w") as fh:
        for q in queries:
            for h in q["hits"]:
                for n, p in enumerate(h["hsps"], 1):
                    seq = p["hseq"].replace("-", "")
                    fh.write(">%s:%d-%d query=%s hsp=%d evalue=%.3g\n" % (h["id"], p["hit_from"], p["hit_to"],
                                                                         q["id"].split()[0], n, p["evalue"]))
                    for i in range(0, len(seq), 70):
                        fh.write(seq[i:i + 70] + "\n")


def fmt_evalue(e):
    return "0.0" if e == 0 else "%.1e" % e if e < 0.01 else "%.2g" % e


def comment_markdown(result, link):
    lines = []
    if result["status"] == "failed":
        lines += ["### ❌ This BLAST search could not run", "", result["error"], "",
                  "Fix the request by opening a new search%s." % (" on the [website](%s)" % link.split("#")[0] if link else "")]
        return "\n".join(lines)
    o = result["request"]
    n_hits = sum(len(q["hits"]) for q in result["queries"])
    lines += ["### ✅ BLAST results are ready", "",
              "**[Open the full results →](%s)**" % link if link else "",
              "", "`%s` vs **%s** · %d quer%s · %d hit%s · %ss" % (
                  o["program"], DB_LABEL[o["database"]], len(result["queries"]),
                  "y" if len(result["queries"]) == 1 else "ies", n_hits, "" if n_hits == 1 else "s", result["runtime_seconds"]), ""]
    for q in result["queries"][:5]:
        lines.append("**%s** (%s)" % (q["id"], format(q["len"], ",")))
        if not q["hits"]:
            lines += ["", "_No hits below the E-value threshold._", ""]
            continue
        lines += ["", "| Subject | Gene | E-value | Identity | Query cover | Location |", "|---|---|---|---|---|---|"]
        for h in q["hits"][:10]:
            p = h["hsps"][0]
            lines.append("| %s | %s | %s | %.1f%% | %.0f%% | %s–%s |" % (
                h["id"], gene_label(h).replace("|", "/") or "–", fmt_evalue(h["evalue"]), h["identity"],
                h["query_cover"], format(p["hit_from"], ","), format(p["hit_to"], ",")))
        if len(q["hits"]) > 10:
            lines.append("")
            lines.append("_…and %d more hits on the results page._" % (len(q["hits"]) - 10))
        lines.append("")
    if len(result["queries"]) > 5:
        lines.append("_%d more queries on the results page._" % (len(result["queries"]) - 5))
    return "\n".join(lines)


def request_text(body, lab_key, out_dir):
    """The request as '### Heading' text, decrypting it in private mode."""
    import labcrypt
    envelope = labcrypt.find_envelope(body)
    if not lab_key:
        if envelope:
            raise RequestError("This request is encrypted, but this BLAST server is not in private mode (no LAB_KEY secret).")
        return body
    if envelope is None:
        # Plain sequences in a private server's issue: have the workflow wipe them.
        open(os.path.join(out_dir, "scrub"), "w").close()
        raise RequestError("This BLAST server is private. Submit searches from the website, which encrypts them.")
    try:
        return labcrypt.decrypt(envelope, lab_key).decode("utf-8")
    except labcrypt.DecryptError:
        raise RequestError("The request could not be decrypted: the lab passphrase has probably changed. "
                           "Re-enter the current passphrase on the website and submit again.")


def encrypt_results(res_dir, lab_key):
    import labcrypt
    for path in glob.glob(os.path.join(res_dir, "*")):
        if not path.endswith(".enc"):
            labcrypt.encrypt_file(path, path + ".enc", lab_key)
            os.remove(path)


def private_comment(result, link, scrubbed):
    if scrubbed:
        return ("### 🔒 This BLAST server is private\n\nSearches have to be submitted from the "
                "[website](%s), which encrypts them. The text of this issue has been removed." % link.split("#")[0])
    where = "[Open the results](%s) (needs the lab passphrase)" % link if link else "Open the results on the website"
    if result["status"] == "done":
        return "### ✅ BLAST results are ready\n\n**%s**" % where
    return "### ❌ This search could not run\n\n%s to see why." % where.replace("Open the results", "Open the search")


def main():
    env = os.environ
    number = env.get("ISSUE_NUMBER", "0")
    if not number.isdigit():
        sys.exit("bad ISSUE_NUMBER")
    db_dir, out_dir = env["DB_DIR"], env["OUT_DIR"]
    res_dir = os.path.join(out_dir, "results", number)
    os.makedirs(res_dir, exist_ok=True)
    site = env.get("SITE_URL", "").rstrip("/")
    link = "%s/#/job/%s" % (site, number) if site else ""
    result = {"job": int(number), "submitted_by": env.get("ISSUE_AUTHOR", ""),
              "submitted": env.get("ISSUE_CREATED", ""), "finished": None}
    lab_key = env.get("LAB_KEY", "")
    t0 = time.time()
    try:
        opts = parse_request(request_text(env.get("ISSUE_BODY", ""), lab_key, out_dir))
        with open(os.path.join(db_dir, "info.json")) as fh:
            info = json.load(fh)
        if opts["database"] not in info["databases"]:
            raise RequestError("The %s database has not been built (was a GFF annotation provided?)." % opts["database"])
        result["request"] = {k: v for k, v in opts.items() if k != "queries"}
        result["database_info"] = dict(info["databases"][opts["database"]], built=info.get("built"),
                                       assembly=info.get("assembly"), species=info.get("species"))
        qpath = os.path.join(res_dir, "query.fa")
        with open(qpath, "w") as fh:
            for name, seq in opts["queries"]:
                fh.write(">%s\n%s\n" % (name, seq))
        archive = os.path.join(out_dir, "blast.asn")
        threads = int(env.get("BLAST_THREADS") or os.cpu_count() or 2)
        cmd = blast_command(opts, os.path.join(db_dir, opts["database"]), qpath, archive, threads)
        print("+ " + " ".join(cmd), flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(env.get("BLAST_TIMEOUT", "6000")))
        if proc.returncode != 0:
            raise RequestError("BLAST stopped with an error:\n\n```\n%s\n```" % proc.stderr.strip()[-2000:])
        result["blast_version"] = subprocess.run([opts["program"], "-version"], capture_output=True, text=True).stdout.split("\n")[0].split(":")[-1].strip()
        jpath = os.path.join(out_dir, "blast.json")
        subprocess.run(["blast_formatter", "-archive", archive, "-outfmt", "15", "-out", jpath], check=True)
        subprocess.run(["blast_formatter", "-archive", archive, "-outfmt", "0", "-out",
                        os.path.join(res_dir, "alignments.txt")], check=True)
        with open(jpath) as fh:
            report = json.load(fh)
        ann = Annotation(os.path.join(db_dir, "annotation.json"))
        result["queries"] = summarise(report, opts, ann)
        write_tsv(os.path.join(res_dir, "hits.tsv"), result["queries"])
        write_hit_fasta(os.path.join(res_dir, "hits.fasta"), result["queries"])
        result["files"] = ["alignments.txt", "hits.tsv", "hits.fasta", "query.fa"]
        result["status"] = "done"
    except RequestError as e:
        result.update(status="failed", error=str(e))
    except subprocess.TimeoutExpired:
        result.update(status="failed", error="The search took too long and was stopped. Try fewer or shorter "
                                              "queries, a stricter E-value, or blastn instead of tblastx.")
    except Exception as e:  # keep the user informed even on an unexpected crash
        import traceback
        traceback.print_exc()
        result.update(status="failed", error="Internal error while running the search (%s). "
                                              "The site maintainer can see details in the Actions log." % type(e).__name__)
    result["runtime_seconds"] = round(time.time() - t0, 1)
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(os.path.join(res_dir, "result.json"), "w") as fh:
        json.dump(result, fh, separators=(",", ":"))
    if lab_key:
        encrypt_results(res_dir, lab_key)
        comment = private_comment(result, link, os.path.exists(os.path.join(out_dir, "scrub")))
    else:
        comment = comment_markdown(result, link)
    with open(os.path.join(out_dir, "comment.md"), "w") as fh:
        fh.write(comment + "\n")
    with open(os.path.join(out_dir, "status"), "w") as fh:
        fh.write(result["status"])
    # In private mode the Actions log is public, so keep error details out of it.
    print("status:", result["status"], "" if lab_key else result.get("error", ""))


if __name__ == "__main__":
    main()
