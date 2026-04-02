# NanoAgent

NanoAgent is a lightweight local Python agent project with:

- multi-threaded user/agent runtime
- pluggable LLM providers
- tool calling
- local RAG storage backends
- trace/span based auditing

## Requirements

- Python 3.13+

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For local development, keep LLM secrets in a `.env` file instead of `config.json`:

```bash
cp .env.example .env
```

If you want to enable the optional ChromaDB storage backend:

```bash
pip install -r requirements-chromadb.txt
```

For development tooling:

```bash
pip install -r requirements-dev.txt
```

## Run

```bash
python3 main.py
```

The app automatically loads `.env` from the project root before startup. Typical variables:

```bash
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
DASHSCOPE_API_KEY=...
```

## Checks

Compile-check the project:

```bash
python3 -m compileall .
```

Or use the Makefile shortcuts:

```bash
make install
make install-dev
make check
make run
```

## Compile

Generate an executable launcher in the project `bin/` directory:

```bash
make compile
./bin/nanoagent
```
