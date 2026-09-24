#!/usr/bin/env python3
"""Write a small synthetic genome + GFF3 for testing the pipeline.

    python3 tests/make_test_data.py OUTDIR

Three random contigs carrying three two-exon genes. Two genes encode real,
highly conserved proteins (histone H3 and ubiquitin), so the example queries
and the tests have something meaningful to find.
"""

import os
import random
import sys

H3 = ("MARTKQTARKSTGGKAPRKQLATKAARKSAPATGGVKKPHRYRPGTVALREIRRYQKSTELLIRKLPFQRLVREIAQDF"
      "KTDLRFQSSAVMALQEASEAYLVGLFEDTNLCAIHAKRVTIMPKDIQLARRIRGERA")
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"

CODONS = {
    "A": "GCC", "R": "CGC", "N": "AAC", "D": "GAC", "C": "TGC", "Q": "CAG", "E": "GAG", "G": "GGC",
    "H": "CAC", "I": "ATC", "L": "CTG", "K": "AAG", "M": "ATG", "F": "TTC", "P": "CCC", "S": "AGC",
    "T": "ACC", "W": "TGG", "Y": "TAC", "V": "GTG",
}
ALT = {"A": "GCT", "R": "AGA", "G": "GGA", "L": "TTG", "S": "TCA", "T": "ACA", "V": "GTA", "K": "AAA",
       "E": "GAA", "P": "CCA", "I": "ATT", "Q": "CAA"}


def back_translate(protein, rng):
    return "".join(ALT[a] if a in ALT and rng.random() < 0.4 else CODONS[a] for a in protein) + "TAA"


def revcomp(s):
    return s[::-1].translate(str.maketrans("ACGT", "TGCA"))


def main(out):
    rng = random.Random(7)
    os.makedirs(out, exist_ok=True)
    rand = lambda n: "".join(rng.choice("ACGT") for _ in range(n))
    random_protein = "M" + "".join(rng.choice("ACDEFGHIKLMNPQRSTVWY") for _ in range(219))

    contigs = {"scaffold_1": rand(60000), "scaffold_2": rand(40000), "scaffold_3": rand(25000)}
    genes = [  # (gene id, name, product, contig, start, strand, protein)
        ("HELE_000101", "H3", "histone H3", "scaffold_1", 10001, "+", H3),
        ("HELE_000202", "UBB", "polyubiquitin-B", "scaffold_2", 15001, "-", UBQ),
        ("HELE_000303", "", "hypothetical protein", "scaffold_3", 5001, "+", random_protein),
    ]
    gff = ["##gff-version 3"]
    for name, seq in contigs.items():
        gff.append("##sequence-region %s 1 %d" % (name, len(seq)))
    for gid, gname, product, ctg, start, strand, prot in genes:
        cds = back_translate(prot, rng)
        cut = (len(cds) // 2) // 3 * 3
        intron = "GTAAGT" + rand(300) + "TTTCAG"
        utr5, utr3 = rand(60), rand(90)
        mrna_plus = utr5 + cds[:cut] + intron + cds[cut:] + utr3  # genomic span, + orientation
        piece = mrna_plus if strand == "+" else revcomp(mrna_plus)
        seq = contigs[ctg]
        contigs[ctg] = seq[:start - 1] + piece + seq[start - 1 + len(piece):]
        end = start + len(piece) - 1
        # exon/CDS coordinates in genome space
        if strand == "+":
            e1 = (start, start + len(utr5) + cut - 1)
            e2 = (e1[1] + len(intron) + 1, end)
            c1 = (start + len(utr5), e1[1])
            c2 = (e2[0], e2[1] - len(utr3))
        else:
            e2 = (start, start + len(utr3) + (len(cds) - cut) - 1)
            e1 = (e2[1] + len(intron) + 1, end)
            c2 = (start + len(utr3), e2[1])
            c1 = (e1[0], e1[1] - len(utr5))
        attrs = "ID=%s" % gid + (";Name=%s" % gname if gname else "")
        gff.append("\t".join([ctg, "test", "gene", str(start), str(end), ".", strand, ".", attrs]))
        tid = gid + "-RA"
        gff.append("\t".join([ctg, "test", "mRNA", str(start), str(end), ".", strand, ".",
                              "ID=%s;Parent=%s;product=%s" % (tid, gid, product.replace(" ", "%20"))]))
        for i, (a, b) in enumerate(sorted([e1, e2]), 1):
            gff.append("\t".join([ctg, "test", "exon", str(a), str(b), ".", strand, ".", "ID=%s.exon%d;Parent=%s" % (tid, i, tid)]))
        phase = {tuple(c1): "0", tuple(c2): str((3 - cut % 3) % 3)}
        for i, (a, b) in enumerate(sorted([c1, c2]), 1):
            gff.append("\t".join([ctg, "test", "CDS", str(a), str(b), ".", strand, phase[(a, b)], "ID=%s.cds;Parent=%s" % (tid, tid)]))

    # Unannotated, diverged copies of H3 (think pseudogenes / missed paralogs)
    # so searches return several hits of different quality, some intergenic.
    aa = "ACDEFGHIKLMNPQRSTVWY"
    for ctg, pos, identity in (("scaffold_1", 40001, 0.93), ("scaffold_2", 30001, 0.75), ("scaffold_3", 15001, 0.55)):
        prot = "".join(c if rng.random() < identity else rng.choice(aa) for c in H3)
        piece = back_translate(prot, rng)
        seq = contigs[ctg]
        contigs[ctg] = seq[:pos - 1] + piece + seq[pos - 1 + len(piece):]

    with open(os.path.join(out, "genome.fa"), "w") as fh:
        for name, seq in contigs.items():
            fh.write(">%s synthetic test contig\n" % name)
            for i in range(0, len(seq), 70):
                fh.write(seq[i:i + 70] + "\n")
    with open(os.path.join(out, "annotation.gff3"), "w") as fh:
        fh.write("\n".join(gff) + "\n")
    print("Wrote %s/genome.fa and %s/annotation.gff3" % (out, out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "test_data")
