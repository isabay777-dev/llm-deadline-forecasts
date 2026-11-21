# LLM deadline forecasts under decode contention

Can a server tell, after 32 generated tokens, whether a response will finish before a deadline? This repository accompanies *Will this language-model response finish on time?* It contains the analysis code, the measurement protocol and a release with the observed outcomes for two model families served by a two-slot llama.cpp deployment on an Apple M5.

## Qwen2.5-0.5B replication (v0.3.0)

The [Qwen evidence release](https://github.com/isabay777-dev/llm-deadline-forecasts/releases/tag/v0.3.0) adds a prospectively registered replication on a second family. On the same M5, the two forecast estimators were refitted using 64 new training and 24 calibration generations. Four pilot episodes determined the confirmation sample size. The 24 confirmation episodes used 384 fresh evaluation executions, with both methods producing predictions for all 375 that were still active at token 32.

| Held-out comparison | Active executions | Direct Brier | Hazard Brier | Direct minus hazard (95% episode interval) |
| --- | ---: | ---: | ---: | --- |
| SmolLM2-135M | 504 | 0.17145 | 0.17336 | −0.00191 [−0.00561, +0.00178] |
| Qwen2.5-0.5B | 375 | 0.14741 | 0.13460 | +0.01281 [−0.00832, +0.03394] |

The practical equivalence margin was ±0.01. Only the SmolLM2 interval fits entirely inside that margin. On Qwen the estimate favors hazard, while the uncertainty interval includes zero and values above the margin. Each row compares the methods within a single study. Prompts, calibrated time limits and model size differ across studies. The live queue experiment in v0.2.0 uses SmolLM2.

To replay the Qwen scores, extract `AI2_Qwen_replication_v0.3.0.zip` into an empty directory and run `python3 replay.py` there. The archive includes the numerical refit, separate pilot and confirmation tables, and the measurement and sampling code. SHA-256 `d7313ce026912f2f52fcf1ce26c21ab747cdd32481618a17503ae49ecdf25239`. [Registered protocol](second_family/PROTOCOL.md) · [Sample-size lock](second_family/confirmation_lock.json) · [Qwen outcome reader](second_family/replay.py)

## Reproduce the SmolLM2 results (v0.2.0)

From the root of a clone, download and extract the [versioned evidence ZIP](https://github.com/isabay777-dev/llm-deadline-forecasts/releases/download/v0.2.0/AI2_evidence_v2.zip). The following commands run on macOS or Linux with Python 3, curl and unzip:

```sh
curl -fL -o AI2_evidence_v2.zip https://github.com/isabay777-dev/llm-deadline-forecasts/releases/download/v0.2.0/AI2_evidence_v2.zip
python3 -c "import hashlib, pathlib, sys; sys.exit(hashlib.sha256(pathlib.Path('AI2_evidence_v2.zip').read_bytes()).hexdigest() != 'c6137bc5e0186ff3fd33c2aa215a7e64599d2dd82968f1d94aaa4872e43e3067')" && unzip -q AI2_evidence_v2.zip -d evidence
python3 replay.py evidence
```

The code uses Python's standard library for replay. The data include all 512 assigned forecasts in the loaded five-history condition (504 active at step 32), 432 client outcomes and the paired sums of prediction error for the training sensitivity analysis. The masked AFT-minus-hazard gap was +0.01097. Allowing both predictors to use the recorded source histories through step 1,024 gave +0.00276. The paired change was −0.00820 (95% episode interval −0.01053 to −0.00588). The live comparison gave +1.43 timely completions per minute for forecast-guided cancellation versus cancellation at expiry (95% interval −3.26 to +6.12).

The data ZIP contains `SHA256SUMS`; the same file is tracked here as `evidence-SHA256SUMS`. Check both against one another. The README in the data ZIP gives worked examples from the two clocks. Its separate numerical refit bundle reproduces training coefficients and calibration from derived features using the listed scientific Python dependencies. The complete native audit below requires the original local author archive. The [live protocol](LIVE_PROTOCOL.md) is a byte-for-byte copy from the frozen corrected collection.

## Audit the original captures (revision 1)

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

The live interval includes both gains and losses. The prespecified comparison leaves the direction of the throughput effect unresolved. These runs cover one frozen model and one deployment. The [release](https://github.com/isabay777-dev/llm-deadline-forecasts/releases/tag/v0.2.0) provides the per-offer table for all 432 policy outcomes.

## Check the reader itself

```sh
python3 check_reader.py "$ARCHIVE"
```

This command repeats all three checks. It changes one bit in a temporary copy of the ZIP, confirms that verification rejects it before replay, and checks that no success receipt exists for the altered copy. The reference output is [reader-check.json](reader-check.json). For model refitting, figure regeneration and audit of the earlier live series, see `ARTIFACT_README.md` inside the verified author ZIP.

## Identify a revision

The ZIP digest identifies the input bytes, and the Git commit identifies the reader and its expected values. Agreement demonstrates consistency of these archived records and calculations. A reconstruction on independently operated hardware would provide separate evidence about repeatability.

Record a new analysis as a separately identified supplement with its input and output digests. For a changed manuscript or author ZIP, add a new revision manifest, point the reader at the new manifest, rerun the checks and preserve the previous revision and its reported comparisons.

## Measurement sources

Hugging Face H4 curated the first-turn prompts from UltraChat. HuggingFaceTB produced SmolLM2-135M-Instruct, Bartowski provided its Q4_K_M quantization and the ggml-org project supplied the llama.cpp server. [UltraChat 200k](https://huggingface.co/datasets/HuggingFaceH4/ultrachat_200k) · [SmolLM2](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct) · [Quantized model revision](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/tree/09816acd5d99df7be770d85ea30822623dab342c) · [Server revision](https://github.com/ggml-org/llama.cpp/tree/b15ca938ad00aa6b3ee6c2edda7363fd02826b18)
