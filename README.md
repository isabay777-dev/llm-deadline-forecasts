# Replaying the AI2 LLM deadline study

After a response has produced 32 tokens, how often does it reach a natural end within a short deadline? This repository checks the archived observations behind two results in revision 1 of the paper. The first result compares forecast accuracy under contention on an Apple M5. The second measures how many responses reach a client within one second under three live server policies.

## Run the reader

Use a local copy of the revision 1 author package. The native logs, model files and original PDF are inside that ZIP. The compact Git repository contains the reader, version-specific checksums and results of running the reader. To use the supplied local project layout, open a terminal in this repository and run the following commands. For a separately supplied ZIP, set `ARCHIVE` to its location.

```sh
ARCHIVE="../../revision_1/AI2_author_package.zip"
python3 verify.py integrity "$ARCHIVE"
python3 verify.py replay "$ARCHIVE"
python3 verify.py live "$ARCHIVE"
```

The first two commands require Python 3.9 or later and its standard library. The live replay also uses the archived scientific Python environment. The tested versions are Python 3.9.6, NumPy 2.0.2, SciPy 1.13.1, scikit-learn 1.6.1, joblib 1.5.3 and threadpoolctl 3.6.0. All three commands run on CPU. No GPU, running LLM server or network connection is required for replay.

Each command checks the entire author ZIP against the digest recorded in [revision_1.json](revision_1.json), then checks its 1,485 payload files against the inner inventory. This also checks the PDF at `paper/AI2_LLM_deadline_forecasts.pdf`. Each replay uses the scorer and native archive pinned in that inventory and runs them in a temporary directory. The JSON printed to the terminal contains the recomputed results. To save it, add `--receipt my-receipt.json` with a fresh filename.

| Command | Observations checked | Result to inspect |
| --- | --- | --- |
| `replay` | Native sampled-token timestamps, end tokens and frozen forecasts for 1,344 foreground executions in 16 held-out episodes | For 504 eligible executions, AFT minus hazard Brier difference +0.01097 (95% interval +0.00199 to +0.01994). Time-scaled direct minus hazard −0.00191 (−0.00561 to +0.00178). Lower Brier error is better. |
| `live` | Accepted V2 episodes, native server events, client receipts and controller decisions in 12 matched blocks, with 144 offers per policy | Timely natural completions numbered 19 for forecast, 15 for expiry and 9 for FIFO. Forecast minus expiry was +0.33 completions per window (95% paired-block interval −0.76 to +1.43). Five regenerated result files match their reference bytes. |

The live interval includes both gains and losses. The prespecified comparison leaves the direction of the throughput effect unresolved. These runs cover one frozen model and one deployment. The separate [evidence directory](../evidence/) in the accompanying bundle provides a de-identified reader view of all 432 policy outcomes.

## Check the reader itself

```sh
python3 check_reader.py "$ARCHIVE"
```

This command repeats all three checks. It changes one bit in a temporary copy of the ZIP, confirms that verification rejects it before replay, and checks that no success receipt exists for the altered copy. The reference output is [reader-check.json](reader-check.json). For model refitting, figure regeneration and audit of the earlier live series, see `ARTIFACT_README.md` inside the verified author ZIP.

## Identify a revision

The ZIP digest identifies the input bytes, and the Git commit identifies the reader and its expected values. Agreement demonstrates consistency of these archived records and calculations. A reconstruction on independently operated hardware would provide separate evidence about repeatability.

Record a new analysis as a separately identified supplement with its input and output digests. For a changed manuscript or author ZIP, add a new revision manifest, point the reader at the new manifest, rerun the checks and preserve the previous revision and its reported comparisons.
