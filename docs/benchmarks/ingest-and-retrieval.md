# Ingest and retrieval baseline

**Dated baseline: 2026-10-10.** These are the first real numbers for ingest, embedding and retrieval. They describe one run on one machine and one AWS account, and are here to be compared against later, not as targets. All data is synthetic.

## Setup

| | |
| --- | --- |
| Dataset | 10 Synthea patients, seed `20261009`, ages 40 to 75, Massachusetts (`data/synthea/generate.sh 10`) |
| Synthea build | Unpinned `master-branch-latest` jar downloaded 2026-10-09, sha256 `018ad7f04f7aacb995804d7d4781c76d5fc714f7f23257ba50daa9eefae224ac` |
| Database | Postgres 16.15 with pgvector 0.8.7 in Docker, on an Apple M1 laptop |
| Embedding model | Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`), 1024 dimensions, normalized, us-east-2 |
| What is embedded | DocumentReference, Condition, Procedure and MedicationRequest chunks, as `"{resource_type} {effective_date}: {text}"` |
| Code | commit `01e7c8d` for the largest patient, `b9c7c9c` for the other nine |

**This baseline is not exactly reproducible.** Its patients came from an unpinned Synthea build that the `master-branch-latest` release tag no longer has to point at. `data/synthea/generate.sh` is now pinned to Synthea v4.0.0, which generates different patients from the same seed (only 3 of the 10 patient ids recur, and those charts differ in content), so a rerun will give different chunk and token counts. The patients were not regenerated or re-embedded after the pin.

## Chunks

| | Count |
| --- | --- |
| Cases (one per patient) | 10 |
| Chunks stored | 18,699 |
| Chunks embedded | 5,653 |
| Embeddable chunks left without a vector | 0 |
| Chunks not embedded by design (labs, vitals, encounters and the rest) | 13,046 |

Per case (the first 8 characters of the case id in the local database; ids are generated at ingest, so they identify rows in this run only):

| Case | Chunks | Embedded | Bedrock calls |
| --- | --- | --- | --- |
| `bc5f0269` | 7,877 | 2,320 | 2,258 |
| `77179562` | 3,146 | 897 | 843 |
| `e30e2c4e` | 3,040 | 702 | 669 |
| `b13b1134` | 1,063 | 378 | 367 |
| `c94c62ae` | 758 | 326 | 316 |
| `9f6aabe3` | 720 | 259 | 251 |
| `9ea813ca` | 622 | 189 | 176 |
| `27fb0a01` | 496 | 166 | 159 |
| `ab091642` | 493 | 177 | 168 |
| `a260e937` | 484 | 239 | 225 |
| **Total** | **18,699** | **5,653** | **5,432** |

Calls are fewer than embedded chunks because identical texts are embedded once and the vector is reused by sha256, within a patient and across patients (29 cross-patient reuses in this run).

## Embedding cost and time

| | Value |
| --- | --- |
| Bedrock calls | 5,432 |
| Estimated tokens (`--estimate`, 4 characters per token) | 575,435 |
| Real tokens (reported by Bedrock) | 617,785, which is 7% over the estimate (about 3.7 characters per token) |
| Wall time | about 91 minutes (37 min 47 s for the largest patient, 53 min 8 s for the other nine) |
| Throttling retries | 0 |
| Concurrency and pacing | at most 5 requests in flight, starts spaced to 60 per minute |

**Cost range: about $0.012 to $0.12.** The two published price figures disagree by a factor of ten, and the official Bedrock pricing page was not checked directly:

| Price per million input tokens | Cost of this run | Source |
| --- | --- | --- |
| $0.02 | $0.012 | [AWS launch blog for Titan Text Embeddings V2](https://aws.amazon.com/blogs/machine-learning/get-started-with-amazon-titan-text-embeddings-v2-a-new-state-of-the-art-embeddings-model-on-amazon-bedrock/) |
| $0.20 | $0.124 | [Cloudprice](https://cloudprice.net/models/amazon.titan-embed-text-v2%3A0) and [Future AGI](https://futureagi.com/llm-cost-calculator/bedrock/amazon-titan-embed-text-v2-0) price trackers |

Not included: a first attempt that was throttled after about 17 seconds and saved nothing (its token count was not recorded), and two 8-token query embeddings.

## Account quotas

Wall time is set by the request quota, not by the model or the code. Values are from the Service Quotas API for this account in us-east-2 on 2026-10-10.

| Model | Quota | This account | AWS default | Status |
| --- | --- | --- | --- | --- |
| Titan Text Embeddings V2 | On-demand requests per minute | 60 | 6,000 | Not adjustable, AWS support case open |
| Titan Text Embeddings V2 | On-demand tokens per minute | 300,000 | 300,000 | Not the limiting factor |
| Nova 2 Lite (`us.` inference profile) | Cross-region requests per minute | 20 | 2,000 | Not adjustable, AWS support case open |

The first embedding attempt ran 5 workers with no pacing, hit the 60 per minute limit within seconds and failed. Requests are now paced to `PRECERTLY_EMBEDDING_REQUESTS_PER_MINUTE` (default 60) and ingest saves every 100 embeddings, so a failed run keeps what it paid for. At the default quota the same job would take a few minutes.

## Retrieval latency

Database time only, median of 30 runs after one warm-up, on the laptop above. The query vector was a stored one, so no Bedrock call is included.

| Case | Full-text only | Hybrid (full-text + vector + RRF) | Observations by code |
| --- | --- | --- | --- |
| `bc5f0269`: 7,877 chunks, 2,320 embedded | 14.3 ms | 29.2 ms | 5.3 ms |
| `c94c62ae`: 758 chunks, 326 embedded | 5.8 ms | 11.3 ms | 1.0 ms |

End to end, including the Titan call to embed the query, a hybrid search took 490 ms (single measurement, case `c94c62ae`). The Bedrock round trip dominates.

## Relevance check: one query, judged by hand

Query: `chronic low back pain physical therapy`, top 5, on case `c94c62ae`. This is the patient with the most notes among the four that have a "Chronic low back pain" Condition. The chart has exactly two chunks that mention low back pain and no physical therapy at all.

| # | Chunk | Date | Full-text rank | Vector rank | Judgment |
| --- | --- | --- | --- | --- | --- |
| 1 | Encounter note, the only note that mentions low back pain | 2014-01-31 | 1 | 2 | Relevant |
| 2 | Condition: Chronic low back pain (SNOMED 278860009), active | 2014-01-31 | 2 | 1 | Relevant |
| 3 | Condition: Chronic pain (SNOMED 82423001), active | 2014-01-31 | 4 | 3 | Related |
| 4 | Procedure: Radiation therapy care, reason breast cancer | 2018-09-12 | 23 | 6 | False match |
| 5 | Procedure: Radiation therapy care, reason breast cancer | 2018-09-19 | 10 | 24 | False match |

- Both chunks that hold the answer are ranked first and second, and both rankings agree on them.
- Results 4 and 5 are wrong. With no physical therapy in the chart, the word "therapy" pulled in radiation therapy for an unrelated diagnosis.
- Search always returns k results, so weak matches fill the tail when the evidence does not exist. The judgment step has to treat such results as no evidence, and an eval case for "similar but irrelevant evidence" should cover it.

On case `bc5f0269`, where the chart has no back pain, the same query returned the three physical therapy procedures and two notes whose plan lists physical therapy. That shows the pipeline works but says nothing about relevance for back pain.

## Limits of this baseline

- One hand-judged query is an anecdote, not a relevance metric. Recall and precision need the labeled eval cases.
- Latency is from a single laptop with ten patients in the table; it says nothing about larger data or concurrent load.
- The vector path was exercised with real Titan vectors here for the first time. Unit tests use a fake bag-of-words embedder.
