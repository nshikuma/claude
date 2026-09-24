#!/usr/bin/env bash
# Download the genome FASTA and GFF into ./input/ for build_db.py.
#
# Two sources, checked in this order:
#   1. GENOME_URL / GFF_URL  (repository secrets) - any direct-download link.
#      Use this to keep the genome file itself private.
#   2. Assets attached to the GitHub release named $RELEASE_TAG (default
#      "genome-data"): the FASTA (*.fa, *.fasta, *.fna, optionally .gz) and the
#      annotation (*.gff, *.gff3, *.gtf, optionally .gz).
#
# Writes input/genome and input/annotation (the latter only if found).
set -euo pipefail
mkdir -p input
tag="${RELEASE_TAG:-genome-data}"

if [ -n "${GENOME_URL:-}" ]; then
  echo "Downloading genome from the GENOME_URL secret"
  curl -fsSL --retry 5 --retry-delay 10 -o input/genome "$GENOME_URL"
  if [ -n "${GFF_URL:-}" ]; then
    echo "Downloading annotation from the GFF_URL secret"
    curl -fsSL --retry 5 --retry-delay 10 -o input/annotation "$GFF_URL"
  fi
else
  echo "Downloading assets of release '$tag'"
  gh release download "$tag" --dir input/release --clobber
  for f in input/release/*; do
    lower=$(basename "$f" | tr '[:upper:]' '[:lower:]')
    case "$lower" in
      *.fa|*.fa.gz|*.fasta|*.fasta.gz|*.fna|*.fna.gz|*.fas|*.fas.gz) mv "$f" input/genome ;;
      *.gff|*.gff.gz|*.gff3|*.gff3.gz|*.gtf|*.gtf.gz) mv "$f" input/annotation ;;
    esac
  done
fi

if [ ! -s input/genome ]; then
  echo "::error::No genome found. Attach genome.fa(.gz) to a release tagged '$tag' or set the GENOME_URL secret."
  exit 1
fi
# A common mistake is a sharing-page link that returns HTML instead of the file.
if head -c 200 input/genome | grep -qi "<html\|<!doctype"; then
  echo "::error::GENOME_URL returned a web page, not a FASTA file. Use a direct-download link."
  exit 1
fi
[ -s input/annotation ] || echo "::warning::No GFF/GTF annotation found; only the genome database will be built."
ls -la input
