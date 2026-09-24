# *Hydroides elegans* BLAST

A free, public BLAST website for the *Hydroides elegans* genome, hosted
entirely on GitHub. It covers the gap until the assembly is on NCBI.

- **All five BLAST programs**: blastn (megablast / dc-megablast / blastn), blastp, blastx, tblastn, tblastx
- **Three databases**: genome assembly, transcripts and proteins (both built from your GFF)
- **Real NCBI BLAST+**, so results are identical to running BLAST locally
- **Genome hits are labelled with genes**: the overlapping annotated gene, or the nearest gene if the hit is intergenic
- NCBI-style results page: hit graphic, sortable/filterable hit table, full alignments,
  downloads (text alignments, TSV for Excel, hit sequences as FASTA), "edit & resubmit"
- **Private lab mode**: the genome stays in a private repository, and searches and results are encrypted
  with a lab passphrase, so nothing about the genome is public before publication
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

- They need a free **GitHub account** (signed in) to submit searches, plus the lab passphrase in private mode.
- After pressing **BLAST**, GitHub opens in a new tab with the search filled in. They click **Create**, and
  the website then shows progress and the results. A search usually takes **1–3 minutes**.
- GitHub also emails them when the results comment is posted.

## Setup (about 20 minutes)

These steps set the site up in **private lab mode**: the genome and every search stay private until
publication, while the website itself stays free on GitHub Pages. (To run it fully public instead, see
[Public mode](#public-mode-after-publication).)

### What stays private

| | Who can see it |
|---|---|
| Genome FASTA and GFF | Nobody. They live in a separate **private** repository |
| The website page itself (search form, help) | Anyone with the link. It contains no genome data |
| Genome stats, example sequences | Only people with the **lab passphrase** |
| Searches (the query sequences) and results | Only people with the **lab passphrase**. GitHub stores only encrypted text |
| Running searches | Only the GitHub usernames you list |
| That a search happened, who ran it, when | Public (the issue titles just say "BLAST: lab search") |

### 1. Create the website repository

Create a new **public** repository (e.g. `hydroides-blast`) and copy everything in this folder into it,
including the hidden `.github` folder:

```bash
git clone https://github.com/YOUR-USER/hydroides-blast.git
cp -R path/to/this/hydroides-blast/. hydroides-blast/
cd hydroides-blast && git add -A && git commit -m "BLAST site" && git push
```

It has to be public for free GitHub Pages hosting. That's fine, because it contains only code, and
everything about the genome is encrypted.

### 2. Put the genome in a separate private repository

1. Create a second repository, e.g. `hydroides-genome-data`, and set it to **Private**.
2. Go to its **Releases → Draft a new release**. Under *Choose a tag*, type `genome-data` and create the tag.
3. Drag in the genome FASTA and the GFF3/GTF. Gzip them first (`gzip genome.fa`); each file can be up to 2 GB.
   Recognised names: `*.fa`, `*.fasta`, `*.fna`, `*.fas` for the genome and `*.gff`, `*.gff3`, `*.gtf` for the
   annotation, each optionally `.gz`.
4. Click **Publish release**.

### 3. Give the website permission to read the private repository

Make a token that can *only read* that one repository:

1. Click your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token**.
2. Name: `hydroides-blast data`. **Expiration**: pick a date after you expect the paper to be out (you can renew it).
3. **Repository access**: *Only select repositories* → `hydroides-genome-data`.
4. **Permissions → Repository permissions → Contents: Read-only**. Leave everything else alone.
5. Generate, and copy the token (it starts with `github_pat_`).

### 4. Add the secrets and settings to the website repository

In the **website** repository: *Settings → Secrets and variables → Actions*.

On the **Secrets** tab, click *New repository secret* for each of these:

| Secret | Value |
|---|---|
| `DATA_TOKEN` | the token from step 3 |
| `LAB_KEY` | the lab passphrase (see below) |

On the **Variables** tab, click *New repository variable* for each of these:

| Variable | Value |
|---|---|
| `DATA_REPO` | `YOUR-USER/hydroides-genome-data` |
| `ALLOWED_USERS` | GitHub usernames allowed to run searches, comma-separated, e.g. `yourlogin,postdoclogin` |

**Choosing the passphrase:** everything published is encrypted with it, so make it long. Four or five random
words (`tidal-copper-worm-lantern-basil`) is good. Anyone with the passphrase can read results, so share it
in person or over a private channel, not by email to a list.

Then, as a precaution: *Settings → Actions → General → Fork pull request workflows from outside collaborators* →
**Require approval for all outside collaborators** → Save.

### 5. Turn on the website

*Settings → Pages → Build and deployment*: Source **Deploy from a branch**, Branch **main**, folder
**/docs** → Save. After a minute the site is live at `https://YOUR-USER.github.io/hydroides-blast/`.

### 6. Build the databases

*Actions tab → **Build BLAST databases** → Run workflow*. If GitHub asks you to enable workflows, click the
button to enable them. For a ~1 Gb genome this takes roughly 10–20 minutes.

### 7. Try it, then invite the lab

Open the site, enter the passphrase and run a search. Then send lab members:
- the website link and the passphrase, and
- a request for their GitHub username, which you add to `ALLOWED_USERS`. They need a free GitHub account.
  They do **not** need access to either repository.

## Settings (optional)

*Settings → Secrets and variables → Actions → **Variables** tab:*

| Variable | Effect |
|---|---|
| `ALLOWED_USERS` | Who may run searches. A comma-separated list of GitHub usernames; or `collaborators` (anyone you add under *Settings → Collaborators*); or empty for anyone with a GitHub account |
| `ASSEMBLY` | Assembly name/version shown on the site, e.g. `HelegV1.0` |
| `SPECIES` | Defaults to `Hydroides elegans` |
| `SITE_URL` | Only needed with a custom domain |
| `DB_VERSION` | Change it (e.g. `1` → `2`) to force a database rebuild |

Also edit `docs/config.js` for the site title and a lab name in the footer.

**Updating the genome:** replace the files on the `genome-data` release in the private data repository, then
run **Build BLAST databases** again. It notices the new files and rebuilds.

**Someone leaves the lab:** remove their username from `ALLOWED_USERS`, which stops them running searches. To
stop them reading *new* results as well, change `LAB_KEY` and tell the lab the new passphrase. Also bump
`DB_VERSION`, because the cached databases are encrypted with the old passphrase. Old results stay readable
only with the old passphrase.

**The token expires:** make a new one (step 3) and replace the `DATA_TOKEN` secret.

## Public mode (after publication)

Once the genome is public, you can drop the passphrase:

1. Delete the `LAB_KEY` secret, bump `DB_VERSION`, and run **Build BLAST databases**.
2. Optionally clear `ALLOWED_USERS` so anyone can search.

If you never set `LAB_KEY`, the site runs in public mode from the start. In that mode, searches and results
are readable by anyone. You can also put the genome release in the website repository itself (then you
don't need `DATA_REPO` or `DATA_TOKEN`), but anyone can download it from there. The `GENOME_URL`/`GFF_URL`
secrets are a third option: any direct-download links, fetched privately.

> ⚠️ Set `LAB_KEY` **before** the first build or search. Anything published before private mode was on
> stays in the repository history.

## Things to know

- **What the public log shows:** in private mode, the Actions logs (public for a public repository) contain no
  sequences, gene names or genome stats. A search posted by mistake as plain text directly on GitHub (not via
  the website) is refused and its text is removed. GitHub keeps the removed text in the issue's edit history,
  which you can delete from the issue page (*edited* → select a revision → *Delete revision from history*).
- **Search limits** (set in `scripts/run_blast.py`): 25 sequences per search; total query length up
  to 200 kb for blastn, 50 kb for blastp/blastx, 20 kb for tblastn, and 10 kb for tblastx (which is slow on
  a large genome). Searches are stopped after ~100 minutes.
- **Speed**: GitHub's free runners have 4 CPUs and 16 GB of RAM. Each search spends ~30–60 s starting
  up, then BLAST itself runs. Several searches can run at the same time.
- **Database cache**: the built databases are kept, encrypted in private mode, in the GitHub Actions cache.
  GitHub deletes caches that go unused for 7 days, so a weekly job keeps it warm. If it's ever missing, the next
  search rebuilds it (and takes longer).
- **Rate limit**: the results page checks GitHub for progress, and GitHub allows about 60 checks per hour
  per network. That's plenty for a lab, and the page tells you if you hit it.
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
| `scripts/labcrypt.py` | Lab-passphrase encryption (AES-256-GCM), matching the website's Web Crypto code |
| `tests/` | End-to-end tests on a synthetic genome (`python3 tests/test_pipeline.py`) |

Run the tests locally with `sudo apt-get install ncbi-blast+ gffread python3-cryptography` (or
`conda install -c bioconda blast gffread cryptography`), then `python3 tests/test_pipeline.py`. To preview the site, run `cd docs && python3 -m http.server`
and open http://localhost:8000. It runs in preview mode with the demo data.
