# LLM token pricing research

**Prices retrieved: 2026-11-07. WARNING: LLM prices change frequently (several vendors below already have scheduled increases). Re-verify at the source URLs before relying on any number.**

All prices are USD per 1M tokens, standard (non-batch, non-priority) tier. Cached-input prices are listed separately and are never the standard input price. Only vendor-owned pages were used. OpenAI and Google pages were read through a summarising fetch tool (the Anthropic page was read from the raw page text), so spot-check those two before committing numbers to production.

## 1. Cloud API models

| Vendor | Model | Input | Output | Notes | Source |
|---|---|---|---|---|---|
| Anthropic | Claude Fable 5.1 | 10.00 | 50.00 | Top tier. Cache hit 0.25. | [1] |
| Anthropic | Claude Opus 5.5 | 4.00 | 20.00 | Cache hit 0.20. | [1] |
| Anthropic | Claude Opus 5 | 5.00 | 25.00 | Cache hit 0.50. | [1] |
| Anthropic | Claude Sonnet 5.5 | 2.00 | 10.00 | Workhorse. Cache hit 0.10. | [1] |
| Anthropic | Claude Sonnet 5 | 2.00 | 10.00 | Introductory price made permanent; the planned rise to 3/15 on 2026-09-01 will not occur (page footnote 3). Cache hit 0.20. | [1] |
| Anthropic | Claude Haiku 5.5 (prompt <= 100k) | 0.10 | 0.50 | Cache hit 0.01. | [1] |
| Anthropic | Claude Haiku 5.5 (prompt > 100k) | 0.50 | 2.50 | Tiered by prompt length per request, counting cache reads/writes. Cache hit 0.05. | [1] |
| Anthropic | Claude Haiku 4.5 | 1.00 | 5.00 | Older small model. Cache hit 0.10. | [1] |
| OpenAI | gpt-6-astra | 10.00 | 50.00 | Cached input 1.00. Above 272K input: 20.00 / 75.00 (cached 2.00). | [2] |
| OpenAI | gpt-6.1-sol | 2.00 | 10.00 | Cached input 0.10. Above 272K: 4.00 / 15.00. | [2] |
| OpenAI | gpt-6.1-terra | 1.00 | 5.00 | Cached input 0.05. Above 272K: 2.00 / 7.50. | [2] |
| OpenAI | gpt-6-luna | 0.10 | 0.50 | Cached input 0.01. Above 272K: 0.20 / 0.75. | [2] |
| OpenAI | gpt-5.5 | 5.00 | 30.00 | Cached input 0.50. Above 272K: 10.00 / 45.00. | [2] |
| OpenAI | gpt-5.4 | 2.50 | 15.00 | Cached input 0.25. Above 272K: 5.00 / 22.50. | [2] |
| OpenAI | gpt-5.4-mini | 0.75 | 4.50 | Cached input 0.075. No tier listed. | [2] |
| OpenAI | gpt-5.4-nano | 0.20 | 1.25 | Cached input 0.02. | [2] |
| OpenAI | gpt-4.1 | 2.00 | 8.00 | Older. Cached input 0.50. | [2] |
| OpenAI | gpt-4o-mini | 0.15 | 0.60 | Older. Cached input 0.075. | [2] |
| Google | Gemini 4 Argon | 2.00 | 10.00 | Doubles to 4.00 / 20.00 from 2026-12-01. Cache 0.10 (0.20 from 2026-12-01). | [3] |
| Google | Gemini 3.8 Flash | 0.75 | 3.75 | Doubles to 1.50 / 7.50 from 2027-01-01. Cache 0.075 plus storage. | [3] |
| Google | Gemini 3.5 Flash-Lite | 0.30 | 2.50 | Cache 0.03 plus storage. | [3] |
| Google | Gemini 3.1 Pro Preview | 2.00 (<=200k) / 4.00 (>200k) | 12.00 / 18.00 | Tiered at 200k prompt tokens. Cache 0.20 / 0.40 plus storage. | [3] |
| Google | Gemini 3.1 Flash-Lite | 0.25 | 1.50 | Text/image/video input; audio input 0.50. | [3] |
| Google | Gemini 2.5 Flash-Lite | 0.10 | 0.40 | Text/image/video input; audio input 0.30. | [3] |

Notes:
- Google output price includes thinking tokens [3]. Anthropic and OpenAI tables list one output price; extended-thinking tokens are billed as output on Anthropic.
- Anthropic: 1M-token context is included at standard price for Claude 4.6 and later except Haiku 5.5 [1]. Anthropic also lists a 1.1x multiplier for US-only inference (`inference_geo`) [1].
- OpenAI "pro" models (gpt-5.5-pro 30/180, etc.) and Batch/Flex/Priority tiers exist but are omitted [2]. The OpenAI page lists many models; only a representative set is shown.
- Gemini model names were copied as the summarising fetch tool reported them; the fetch covered the first 100,000 of 103,573 characters of the Google page, so the last ~3.5k characters (not seen) may hold further rows.
- Google's pricing page redirected from ai.google.dev to aistudio.google.com; OpenAI's from platform.openai.com to developers.openai.com; Anthropic's from docs.claude.com to platform.claude.com. All are the vendors' own domains.

## 2. Self-hosted cost estimation (vLLM on owned or rented GPUs)

### Formula

```
cost per 1M tokens = (GPU hourly cost x number of GPUs) / (tokens per hour / 1,000,000)
```

Self-hosted prices have no vendor list price, so this is derived. Input and output tokens must be apportioned because prefill (input) is compute-bound and runs far faster per token than decode (output), which is memory-bandwidth-bound. A common approach: compute the full GPU-hour cost, then split it by the GPU time each kind of token consumes. If prefill runs at P tokens/s and decode at D tokens/s (aggregate across the batch), then input price = hourly cost / (P x 3600) x 1e6 and output price = hourly cost / (D x 3600) x 1e6, each at the utilisation you assume. No primary source prescribing a specific input:output split was found (see gaps); the prefill-to-decode ratio below is an assumption.

### GPU hourly rate inputs (primary sources)

| Provider | GPU | Rate | Basis | Source |
|---|---|---|---|---|
| Lambda | A100 80GB (HGX, 8x) | $3.19 per GPU-hr | On-demand list price; page says prices take effect 2026-11-16 | [4] |
| Lambda | H100 80GB (HGX, 8x) | $4.59 per GPU-hr | On-demand; 4x SXM $4.69, 2x $4.79, 1x $4.89, 1x PCIe $3.79 | [4] |
| AWS | A100 (p4d.24xlarge, 8x 40GB) | $1.696 per GPU-hr ($13.57/instance) | EC2 Capacity Blocks reservation rate, NOT on-demand | [5] |
| AWS | A100 80GB (p4de.24xlarge, 8x) | $2.546 per GPU-hr ($20.369/instance) | Capacity Blocks, NOT on-demand | [5] |
| AWS | H100 (p5.48xlarge, 8x) | $5.970 per GPU-hr ($47.757/instance) | Capacity Blocks, NOT on-demand | [5] |

Gaps: AWS on-demand instance prices, Google Cloud (a2-ultragpu / a3-highgpu) and Azure (NC A100 v4 / ND H100 v5) hourly prices could not be extracted. Those pages either render prices dynamically or the fetch returned truncated content [6][7][8][9]. No cloud-provider figure for these three is quoted here. Owned-hardware amortisation: no primary source was found discussing it, so no purchase price or amortisation figure is given. If needed, compute it as (purchase price + power/hosting over the life) / (hours of life), and treat that as the team's own input.

### Throughput inputs (primary sources, with limits)

- Qwen (model vendor) docs, Qwen2.5-32B-Instruct on one A100 80GB with vLLM 0.6.3, BF16: 22.13 tokens/s at 1 input token, 21.05 at 6144, 19.91 at 14336 [10]. This is explicitly batch size 1, so it is a single-stream floor, not serving capacity. Continuous batching in vLLM multiplies aggregate throughput many times over.
- NVIDIA TensorRT-LLM perf overview (hardware vendor, not vLLM), Llama 3.3 70B, H100, TP2, FP8: 2,209 output tokens/s/GPU at 1000/1000 input/output tokens, and 398 at 8192/1024; offline max-throughput, all requests queued at once [11]. This is an upper-bound style figure from a different engine.
- Gap: no first-party vLLM page with batched aggregate throughput for a 32B-70B model was found. The vLLM benchmark search returned only third-party pages, which were excluded. The figures above bracket the range (about 22 tokens/s single stream to about 2,200 tokens/s/GPU batched), so the aggregate figure used below is an assumption between them.

### Worked arithmetic: 32B-class model

Assumptions (all stated, none from a vendor price):
- One H100 80GB serves a 32B model in FP8 or a quantised form, or two A100 80GB serve BF16. Rate: Lambda H100 at $4.59/GPU-hr [4].
- Aggregate decode throughput per GPU under realistic batching: 1,000 output tokens/s (conservative, roughly half of NVIDIA's 70B FP8 per-GPU figure [11]; a 32B model is smaller, which offsets lower real-world efficiency).
- Prefill is assumed 10x faster than decode per token, so 10,000 input tokens/s per GPU.
- Average utilisation 50 percent (idle time, uneven load, on-prem sizing headroom).

Output: 1,000 tok/s x 3,600 = 3.6M tokens/hr at full load. Cost at full load = $4.59 / 3.6 = $1.28 per 1M output tokens. At 50 percent utilisation: $1.28 / 0.5 = about $2.55 per 1M output tokens.

Input: 10,000 tok/s x 3,600 = 36M tokens/hr. Cost at full load = $4.59 / 36 = $0.13 per 1M input tokens. At 50 percent: about $0.26 per 1M input tokens.

A100 80GB cross-check: $3.19/hr [4], assume 500 output tok/s per GPU (older GPU, about half): $3.19 / 1.8 = $1.77 at full load, about $3.5 at 50 percent.

Conclusion: a defensible order of magnitude for a self-hosted 32B-class model is roughly $0.1-0.3 per 1M input tokens and $1-4 per 1M output tokens, i.e. USD 0.25 input and 2.00 output as round config numbers. The result is sensitive to the throughput and utilisation assumptions (each can move the answer by 2-5x); measure real throughput on the actual servers with vLLM's own benchmark scripts before trusting it. For owned hardware with sunk cost, the marginal rate can be lower than the cloud rental rate used here.

## 3. Suggested config values

Rounded, for a pricing config with a realistic spread (USD per 1M tokens). Cloud values come from the table above; the self-hosted values are a derived estimate.

| Config key | Tier | Input | Output | Basis |
|---|---|---|---|---|
| claude-opus-5-5 | cloud, flagship | 4.00 | 20.00 | [1] |
| claude-sonnet-5-5 | cloud, workhorse | 2.00 | 10.00 | [1] |
| claude-haiku-5-5 | cloud, small (prompt <= 100k only) | 0.10 | 0.50 | [1] |
| gpt-5.4-mini | cloud, mid-small | 0.75 | 4.50 | [2] |
| gemini-3.5-flash-lite | cloud, small | 0.30 | 2.50 | [3] |
| selfhost-qwen-32b-vllm | self-hosted 32B | 0.25 | 2.00 | derived, section 2 |

Optionally add a second self-hosted entry for a 70B-class model at about 0.50 input / 4.00 output (roughly 2x the 32B estimate; not independently sourced). The tool should keep cached-input and tiered prices out of the basic config unless the logs report cached tokens.

## 4. Sources

1. Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing (redirect target of https://docs.claude.com/en/docs/about-claude/pricing)
2. OpenAI API pricing: https://developers.openai.com/api/docs/pricing (redirect target of https://platform.openai.com/docs/pricing)
3. Google Gemini API pricing: https://aistudio.google.com/docs/pricing (redirect target of https://ai.google.dev/gemini-api/docs/pricing)
4. Lambda pricing: https://lambda.com/pricing (redirect target of https://lambda.ai/pricing)
5. AWS EC2 Capacity Blocks pricing: https://aws.amazon.com/ec2/capacityblocks/pricing/
6. AWS P4 instances page (no prices listed): https://aws.amazon.com/ec2/instance-types/p4/
7. AWS EC2 on-demand pricing (no GPU instance prices extracted): https://aws.amazon.com/ec2/pricing/on-demand/
8. Google Cloud GPU pricing (content truncated, no prices): https://cloud.google.com/compute/gpus-pricing and https://cloud.google.com/compute/all-pricing
9. Azure Linux VM pricing (table not rendered, no prices): https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/
10. Qwen2.5 speed benchmark: https://qwen.readthedocs.io/en/v2.5/benchmark/speed_benchmark.html
11. NVIDIA TensorRT-LLM performance overview: https://nvidia.github.io/TensorRT-LLM/developer-guide/perf-overview.html
