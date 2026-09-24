#!/usr/bin/env python3
"""Build the three BLAST databases (genome, transcripts, proteins) from a
genome FASTA and a GFF3/GTF annotation.

    python3 scripts/build_db.py --genome genome.fa.gz --gff annotation.gff3.gz --out blastdb

Needs NCBI BLAST+ (makeblastdb) and gffread on the PATH. Both inputs may be
gzipped. Writes into --out:

    genome.*        nucleotide BLAST db of the assembly
    transcripts.*   nucleotide BLAST db of spliced transcripts (from the GFF)
    proteins.*      protein BLAST db of translated CDS (from the GFF)
    annotation.json gene coordinates and names, used to label hits
    info.json       summary stats + example queries, shown on the website
"""

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote


def is_gzip(path):
    with open(path, "rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def open_text(path):
    if is_gzip(path):
        return gzip.open(path, "rt")
    return open(path)


def read_fasta(path):
    name, seq = None, []
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(seq)
                name, seq = line[1:], []
            elif line:
                seq.append(line.strip())
    if name is not None:
        yield name, "".join(seq)


def write_clean_genome(src, dst):
    """Copy the genome, keeping only the first word of each header (BLAST's
    -parse_seqids needs short, simple IDs). Returns per-sequence lengths and
    GC counts."""
    stats = {"lengths": [], "gc": 0, "acgt": 0}
    seen = set()
    with open(dst, "w") as out:
        for header, seq in read_fasta(src):
            sid = header.split()[0] if header.strip() else "seq%d" % (len(seen) + 1)
            if len(sid) > 50:
                sys.exit("Sequence ID longer than 50 characters (BLAST limit): %s" % sid)
            if sid in seen:
                sys.exit("Duplicate sequence ID in genome: %s" % sid)
            seen.add(sid)
            seq = seq.upper()
            stats["lengths"].append(len(seq))
            stats["gc"] += seq.count("G") + seq.count("C")
            stats["acgt"] += len(seq) - seq.count("N")
            out.write(">%s\n" % sid)
            for i in range(0, len(seq), 80):
                out.write(seq[i:i + 80] + "\n")
    return stats


def n50(lengths):
    total, running = sum(lengths), 0
    for length in sorted(lengths, reverse=True):
        running += length
        if running * 2 >= total:
            return length
    return 0


# --- annotation --------------------------------------------------------------

GTF_ATTR = re.compile(r'(\S+)\s+"([^"]*)"')
DESC_KEYS = ("product", "description", "Note", "note", "function", "gene_description")
NAME_KEYS = ("Name", "gene_name", "gene", "Alias", "gene_symbol")


def parse_attrs(field):
    if "=" in field and '"' not in field.split("=")[0]:
        attrs = {}
        for part in field.strip().strip(";").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                attrs[k.strip()] = unquote(v.strip())
        return attrs
    return {k: v for k, v in GTF_ATTR.findall(field)}  # GTF


def first(attrs, keys):
    for k in keys:
        if attrs.get(k):
            return attrs[k]
    return ""


def parse_annotation(gff_path):
    """Return (genes, transcripts).

    genes:       {gene_id: {seqid, start, end, strand, name, desc}}
    transcripts: {transcript_id: {gene, name, desc, seqid, start, end, strand}}
    Works for GFF3 (gene/mRNA with ID/Parent) and GTF (gene_id/transcript_id).
    """
    genes, transcripts = {}, {}
    tx_types = {"mrna", "transcript", "ncrna", "lnc_rna", "trna", "rrna", "pseudogenic_transcript"}
    with open_text(gff_path) as fh:
        for line in fh:
            if line.startswith("##FASTA"):
                break
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9:
                continue
            seqid, _, ftype, start, end, _, strand, _, attr_field = cols[:9]
            attrs = parse_attrs(attr_field)
            ft = ftype.lower()
            loc = {"seqid": seqid, "start": int(start), "end": int(end), "strand": strand}
            if ft == "gene" or ft == "pseudogene":
                gid = attrs.get("ID") or attrs.get("gene_id")
                if gid:
                    genes[gid] = dict(loc, name=first(attrs, NAME_KEYS), desc=first(attrs, DESC_KEYS))
            elif ft in tx_types:
                tid = attrs.get("ID") or attrs.get("transcript_id")
                gid = (attrs.get("Parent") or attrs.get("gene_id") or tid or "").split(",")[0]
                if tid:
                    transcripts[tid] = dict(loc, gene=gid, name=first(attrs, NAME_KEYS),
                                            desc=first(attrs, DESC_KEYS))
            elif "gene_id" in attrs:
                # GTF files often have only exon/CDS lines: grow the gene and
                # transcript spans from those.
                gid = attrs["gene_id"]
                tid = attrs.get("transcript_id")
                for table, key in ((genes, gid), (transcripts, tid)):
                    if not key:
                        continue
                    rec = table.get(key)
                    if rec is None:
                        rec = table[key] = dict(loc, name=first(attrs, NAME_KEYS), desc="")
                        if table is transcripts:
                            rec["gene"] = gid
                    else:
                        rec["start"] = min(rec["start"], loc["start"])
                        rec["end"] = max(rec["end"], loc["end"])

    # Fill gene names/descriptions from transcripts and vice versa.
    for tid, tx in transcripts.items():
        gene = genes.get(tx["gene"])
        if gene is None:
            genes[tx["gene"] or tid] = {k: tx[k] for k in ("seqid", "start", "end", "strand", "name", "desc")}
            continue
        if not gene["name"] and tx["name"] and tx["name"] != tid:
            gene["name"] = tx["name"]
        if not gene["desc"] and tx["desc"]:
            gene["desc"] = tx["desc"]
        tx["name"] = tx["name"] if tx["name"] and tx["name"] != tid else gene["name"]
        tx["desc"] = tx["desc"] or gene["desc"]
    return genes, transcripts


# --- main --------------------------------------------------------------------

def run(cmd):
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def makeblastdb(fasta, dbtype, out, title):
    run(["makeblastdb", "-in", fasta, "-dbtype", dbtype, "-out", out,
         "-parse_seqids", "-title", title, "-hash_index"])


def clean_proteins(src, dst):
    """gffread -S writes '*' for stops. Drop the terminal stop and skip empty
    or duplicate records. Returns the sequences kept (for stats/examples)."""
    kept = {}
    with open(dst, "w") as out:
        for header, seq in read_fasta(src):
            sid = header.split()[0]
            seq = seq.rstrip("*.")
            if not seq or sid in kept:
                continue
            kept[sid] = seq
            out.write(">%s\n" % header)
            for i in range(0, len(seq), 80):
                out.write(seq[i:i + 80] + "\n")
    return kept


def pick_example(seqs, lo, hi, maxlen):
    """A mid-sized, low-ambiguity sequence to offer as an example query."""
    best = None
    for sid, seq in seqs.items():
        if lo <= len(seq) <= hi and seq.upper().count("X") + seq.upper().count("N") == 0:
            best = (sid, seq)
            break
    if best is None and seqs:
        best = max(seqs.items(), key=lambda kv: len(kv[1]))
    if best is None:
        return None
    return {"id": best[0], "seq": best[1][:maxlen]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--genome", required=True)
    ap.add_argument("--gff", help="GFF3 or GTF annotation (optional: without it only the genome db is built)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--species", default="Hydroides elegans")
    ap.add_argument("--assembly", default="", help="Assembly name/version shown on the website")
    args = ap.parse_args()

    for tool in ("makeblastdb",) + (("gffread",) if args.gff else ()):
        if not shutil.which(tool):
            sys.exit("%s not found on PATH" % tool)

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "_work")
    os.makedirs(work, exist_ok=True)
    t0 = time.time()

    genome_fa = os.path.join(work, "genome.fa")
    gstats = write_clean_genome(args.genome, genome_fa)
    if not gstats["lengths"]:
        sys.exit("Genome FASTA is empty: %s" % args.genome)
    makeblastdb(genome_fa, "nucl", os.path.join(args.out, "genome"), "%s genome" % args.species)

    info = {
        "species": args.species,
        "assembly": args.assembly,
        "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "blast_version": subprocess.run(["makeblastdb", "-version"], capture_output=True, text=True)
        .stdout.splitlines()[0].split(":", 1)[-1].strip(),
        "databases": {
            "genome": {
                "sequences": len(gstats["lengths"]),
                "total_length": sum(gstats["lengths"]),
                "n50": n50(gstats["lengths"]),
                "longest": max(gstats["lengths"]),
                "gc_percent": round(100.0 * gstats["gc"] / gstats["acgt"], 2) if gstats["acgt"] else None,
            }
        },
        "examples": {},
    }

    annotation = {"genes": {}, "transcripts": {}}
    if args.gff:
        gff = args.gff
        if is_gzip(gff):
            gff = os.path.join(work, "annotation.gff")
            with open_text(args.gff) as src, open(gff, "w") as dst:
                shutil.copyfileobj(src, dst)
        genes, transcripts = parse_annotation(gff)

        tx_fa, prot_raw, prot_fa = (os.path.join(work, n) for n in ("transcripts.fa", "proteins_raw.fa", "proteins.fa"))
        run(["gffread", gff, "-g", genome_fa, "-w", tx_fa, "-y", prot_raw, "-S"])

        tx_seqs = {h.split()[0]: s for h, s in read_fasta(tx_fa)}
        proteins = clean_proteins(prot_raw, prot_fa)

        if tx_seqs:
            makeblastdb(tx_fa, "nucl", os.path.join(args.out, "transcripts"), "%s transcripts" % args.species)
            lens = [len(s) for s in tx_seqs.values()]
            info["databases"]["transcripts"] = {"sequences": len(lens), "total_length": sum(lens)}
            info["examples"]["nucleotide"] = pick_example(tx_seqs, 600, 3000, 600)
        if proteins:
            makeblastdb(prot_fa, "prot", os.path.join(args.out, "proteins"), "%s proteins" % args.species)
            lens = [len(s) for s in proteins.values()]
            info["databases"]["proteins"] = {"sequences": len(lens), "total_length": sum(lens)}
            info["examples"]["protein"] = pick_example(proteins, 150, 600, 600)
        info["databases"]["genome"]["genes"] = len(genes)

        annotation = {
            "genes": {gid: [g["seqid"], g["start"], g["end"], g["strand"], g["name"], g["desc"]]
                      for gid, g in genes.items()},
            "transcripts": {tid: [t["gene"], t["name"], t["desc"], t["seqid"], t["start"], t["end"], t["strand"]]
                            for tid, t in transcripts.items()},
        }
        print("Annotation: %d genes, %d transcripts, %d proteins" % (len(genes), len(tx_seqs), len(proteins)))
    else:
        print("No GFF given: only the genome database was built.")

    if "nucleotide" not in info["examples"]:
        # No transcripts: take a slice from the middle of the longest contig.
        for header, seq in read_fasta(genome_fa):
            if len(seq) == info["databases"]["genome"]["longest"]:
                mid = len(seq) // 2
                chunk = seq[mid:mid + 500]
                if chunk.count("N") == 0:
                    info["examples"]["nucleotide"] = {"id": "%s:%d-%d" % (header, mid + 1, mid + 500), "seq": chunk}
                break

    with open(os.path.join(args.out, "annotation.json"), "w") as fh:
        json.dump(annotation, fh, separators=(",", ":"))
    info["build_seconds"] = round(time.time() - t0)
    with open(os.path.join(args.out, "info.json"), "w") as fh:
        json.dump(info, fh, indent=1)
    shutil.rmtree(work)
    print(json.dumps(info["databases"], indent=1))


if __name__ == "__main__":
    main()
