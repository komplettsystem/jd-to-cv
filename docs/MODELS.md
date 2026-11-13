# Models

The admin's Models page lists the models the app may use. Each entry has a provider, a model name, a price per million
tokens, an output mode and a reasoning effort. "Refresh" adds the models a provider currently offers.

## Providers

| Provider | Needs | Notes |
|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | one key and one bill for many makers; the app asks it to use only providers that say they do not collect or train on prompts |
| Amazon Bedrock | an AWS login or role, model access in the console | some regions need an inference-profile id (for example `eu.` prefixed) |
| Anthropic API | `ANTHROPIC_API_KEY` | |
| Local `claude` command, Ollama | nothing | development only, never billed |
| Baseline | nothing | keyword overlap, a floor to compare models against |

## Choosing

Public leaderboards (for example Artificial Analysis) are a way to shortlist models by score and cost. They measure
general tasks with their own settings, not matching a CV to a posting. Decide with the evals in this app (`docs/EVALS.md`)
on your own postings, and look at the cost of a whole report, not only the price per token.

## Reasoning effort

Many models think before they answer, and how long they think changes both quality and cost a lot. Each entry has an
effort setting (minimal to maximum, or the model's default). The app passes it as Pydantic AI's unified `thinking`
setting. Chat answers always use low effort. Not every model accepts every level: the first run shows it.

## Spend caps

- per report (a visitor's check with its chat and letters),
- per day for all visitors together,
- per month for everything that costs real money.

Models without a known price cannot serve visitors, because the caps could not count them. A price comes from the catalog
(OpenRouter's own price, or one entered for a Bedrock model). Running jobs reserve a share of the caps while they run.

## Output mode

Most models return structured output through tool calling. A few cannot; for those the entry is set to "prompted" and the
app asks for JSON in the prompt instead. Check it with the `synthetic-sanity` eval before using such a model.

## Tool calls

The chat can call tools: the match engine (`rescore`, `explain_scoring`) and the matcher (`ask_matcher`, the report's judge over the
whole CV). A catalog row with "tools" off, which today means the baseline, the local `claude` command and Ollama models Ollama does not
list as tool-capable, gets a chat that answers from word-matched CV lines and cannot ask either; the Models page says so next to the
row and under the chat roles, and names up to two similar models that have tool calls. Visitors' chats run on a priced model, so this
affects only local development. A refresh from Ollama asks `/api/show` for each model and sets tools on only when its `capabilities`
list contains `tools`; an older Ollama that sends no list leaves the model without tools. Ollama lists the models that support tool
calling at https://ollama.com/search?c=tool. Models set to the "prompted" output mode (Muse, Qwen, NVIDIA) are marked as having tools.
In one run of the `chat-cv` eval (`docs/EVALS.md`, 2026-11-13) NVIDIA Nemotron 3 Super, which answers in prompted JSON, made ordinary
tool calls to the match engine and to the matcher. Muse and Qwen have not been tried.
