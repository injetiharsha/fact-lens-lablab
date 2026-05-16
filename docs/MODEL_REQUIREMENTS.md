# Checkpoints And Model Requirements

This hackathon build does not require local model checkpoints.

## Required Runtime Providers

- DuckDuckGo via `ddgs`: free live evidence search.
- Gemini Flash: Intake Agent and optional evidence helper.
- Gemini Pro: Consensus Moderator Agent.
- Featherless: Skeptic Agent using an open-source model.

## Environment Variables

```text
GEMINI_API_KEY=
GEMINI_SEARCH_MODEL=gemini-1.5-flash
GEMINI_INTAKE_MODEL=gemini-1.5-flash
GEMINI_MODERATOR_MODEL=gemini-1.5-pro

FEATHERLESS_API_KEY=
FEATHERLESS_API_BASE=https://api.featherless.ai/v1/chat/completions
FEATHERLESS_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct

TAVILY_API_KEY=
FACTLENS_ALLOW_OFFLINE_FALLBACK=0
```

## Why No Checkpoints

The previous FactLens project used local checkpoints and large artifacts. This build intentionally avoids them because:

- Hosted APIs are faster to deploy during the hackathon.
- The archive stays small.
- Vultr/Azure deployment does not need GPU storage.
- Losing VM ephemeral files will not delete model weights because no local weights are required.

## If You Add Local Checkpoints Later

Use this layout and do not commit the binary weights:

```text
checkpoints/
  stance/
  relevance/
  README.md
```

Track only a `README.md` that explains:

- model name,
- source URL,
- expected files,
- checksum,
- exact environment variable needed by the app.

Keep actual checkpoint files in durable object storage or an attached persistent disk, not in VM temporary storage.
