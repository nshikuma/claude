# *Hydroides elegans* BLAST

A free, public BLAST website for the *Hydroides elegans* genome, hosted
entirely on GitHub. It covers the gap until the assembly is on NCBI.

- **All five BLAST programs**: blastn (megablast / dc-megablast / blastn), blastp, blastx, tblastn, tblastx
- **Three databases**: genome assembly, transcripts and proteins (both built from your GFF)
- **Real NCBI BLAST+**, so results are identical to running BLAST locally
- **Genome hits are labelled with genes**: the overlapping annotated gene, or the nearest gene if the hit is intergenic
- NCBI-style results page: hit graphic, sortable/filterable hit table, full alignments,
  downloads (text alignments, TSV for Excel, hit sequences as FASTA), "edit & resubmit"
- Works on phones, has a dark mode, and needs no server or hosting bill

## How it works

```
 Website (GitHub Pages)            GitHub Issues                 GitHub Actions
 ──────────────────────           ─────────────                 ──────────────
 paste sequence, pick db  ──►  "BLAST: ..." issue  ──►  runs NCBI BLAST+ on the genome,
 & program, press BLAST        (user clicks Create)      writes results to the blast-results
                                                         branch, comments + closes the issue
 results page appears    ◄───────────────────────────────────────────┘
 automatically
```

GitHub Pages can only serve static files, and a gigabase genome is far too big
to BLAST inside a web browser. So the website is only the front end: each
search becomes a GitHub issue, and a GitHub Actions workflow runs the search
on GitHub's servers. For a public repository all of this is free.

What this means for the person searching:

- They need a free **GitHub account** (signed in) to submit searches. Looking at results needs no account.
- After pressing **BLAST**, GitHub opens in a new tab with the search filled in. They click **Create**, and
  the website then shows progress and the results. A search usually takes **1–3 minutes**.
- GitHub also emails them when the results comment is posted.

## Setup (about 15 minutes)

### 1. Create the repository

Create a new **public** repository (e.g. `hydroides-blast`) and copy everything
in this folder into it, including the hidden `.github` folder:

```bash
git clone https://github.com/YOUR-USER/hydroides-blast.git
cp -R path/to/this/hydroides-blast/. hydroides-blast/
cd hydroides-blast && git add -A && git commit -m "BLAST site" && git push
```

(GitHub Pages is only free on public repositories. With GitHub Pro/Team a private repo works too,
but then Actions minutes are limited to your plan's allowance.)

### 2. Upload the genome and annotation

Option A, simplest: attach them to a release.

1. On the repo page, go to **Releases → Draft a new release**.
2. Under **Choose a tag**, type `genome-data` and create the tag.
3. Drag in the genome FASTA and the GFF3/GTF. Gzip them first to save time (`gzip genome.fa`).
   Recognised names: `*.fa`, `*.fasta`, `*.fna`, `*.fas` for the genome and `*.gff`, `*.gff3`, `*.gtf` for
   the annotation, each optionally `.gz`. Release files can be up to 2 GB each.
4. Click **Publish release**.

> ⚠️ In a public repository, **anyone can download release files**. If the
> genome must stay private until publication, use option B instead.

Option B, private: keep the files elsewhere. Put them anywhere that gives a direct download link
(a university server, S3, Dropbox with `?dl=1`, and so on). Then add two
**repository secrets** (*Settings → Secrets and variables → Actions → New repository secret*):

| Secret | Value |
|---|---|
| `GENOME_URL` | direct link to the genome FASTA (optionally gzipped) |
| `GFF_URL` | direct link to the GFF3/GTF |

Secrets are hidden from everyone, including the workflow logs. Visitors can only see the sequence of the
regions their searches hit, just as on any BLAST server.

### 3. Turn on the website

*Settings → Pages → Build and deployment*: Source **Deploy from a branch**, Branch **main**, folder
**/docs** → Save. After a minute the site is live at `https://YOUR-USER.github.io/hydroides-blast/`.

### 4. Build the databases

*Actions tab → **Build BLAST databases** → Run workflow*. For a ~1 Gb genome this takes roughly
10–20 minutes. When it's green, the website shows the genome stats, and the example buttons use real
sequences from your genome.

(If GitHub asks you to enable workflows on the Actions tab, click the button to enable them.)

### 5. Try it, then send the link to your postdoc

Run one search yourself. The first search after a build takes a little longer.

## Settings (optional)

*Settings → Secrets and variables → Actions → **Variables** tab:*

| Variable | Effect |
|---|---|
| `ALLOWED_USERS` | Who may run searches. Empty = anyone with a GitHub account. `collaborators` = only people you've added to the repo (*Settings → Collaborators*). Or a comma-separated list of GitHub usernames, e.g. `mylogin,postdoc-login`. |
| `ASSEMBLY` | Assembly name/version shown on the site, e.g. `HelegV1.0` |
| `SPECIES` | Defaults to `Hydroides elegans` |
| `SITE_URL` | Only needed with a custom domain |
| `DB_VERSION` | Change it (e.g. `1` → `2`) to force a database rebuild |

Also edit `docs/config.js` for the site title and a lab name in the footer.

**Updating the genome:** replace the files on the `genome-data` release (or the files behind the URLs,
then bump `DB_VERSION`), and run **Build BLAST databases** again. The databases rebuild automatically
when the release files change.

## Things to know

- **Everything in a public repo is public**: the searches (issues) and their results (the
  `blast-results` branch). Set `ALLOWED_USERS` to stop strangers running searches.
- **Search limits** (set in `scripts/run_blast.py`): 25 sequences per search; total query length up
  to 200 kb for blastn, 50 kb for blastp/blastx, 20 kb for tblastn, and 10 kb for tblastx (which is slow on
  a large genome). Searches are stopped after ~100 minutes.
- **Speed**: GitHub's free runners have 4 CPUs and 16 GB of RAM. Each search spends ~30–60 s starting
  up, then BLAST itself runs. Several searches can run at the same time.
- **Database cache**: the built databases are kept in the GitHub Actions cache. GitHub deletes caches that go
  unused for 7 days, so a weekly job keeps it warm. If it's ever missing, the next search simply rebuilds it
  (and takes longer).
- **Rate limit**: the results page checks GitHub for progress, and GitHub allows about 60 checks per hour
  per network. That's plenty for a lab, and the page tells you if you hit it. Results on the GitHub issue are
  never affected.
- **Searching without the website**: open an issue with the **BLAST search** template. It's the same form.
- **Demo results** from a small synthetic genome are at `…/#/job/demo-tblastn`. They are handy for showing people
  what results look like before the real genome is loaded.

## Files

| Path | What it does |
|---|---|
| `docs/` | The website (plain HTML/CSS/JS, no build step) |
| `.github/workflows/run-blast.yml` | Runs a search when a `BLAST:` issue is opened |
| `.github/workflows/build-db.yml` | Builds/refreshes the databases; weekly keep-alive |
| `.github/actions/blastdb/` | Shared step: install BLAST+, restore or build databases |
| `scripts/build_db.py` | Genome + GFF → BLAST databases, gene index, stats |
| `scripts/run_blast.py` | Validates a request, runs BLAST, writes results and the issue comment |
| `tests/` | End-to-end tests on a synthetic genome (`python3 tests/test_pipeline.py`) |

Run the tests locally with `sudo apt-get install ncbi-blast+ gffread` (or `conda install -c bioconda blast gffread`),
then `python3 tests/test_pipeline.py`. To preview the site, run `cd docs && python3 -m http.server`
and open http://localhost:8000. It runs in preview mode with the demo data.
