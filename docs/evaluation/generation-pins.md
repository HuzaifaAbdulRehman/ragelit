# Local generation baseline

The first CPU smoke uses Qwen2.5-1.5B-Instruct Q4_K_M with llama.cpp.
This small model was selected before results. It is a baseline, not a claim
about production answer quality.

## Verified assets

Download public files without authentication into a new ignored folder.
Verify both byte size and SHA-256 before extracting or loading. Keep the model
card and server license beside the downloads; do not commit weights or binaries.

The [official model repository](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/tree/91cad51170dc346986eccefdc2dd33a9da36ead9)
is Qwen/Qwen2.5-1.5B-Instruct-GGUF. Its immutable revision is
`91cad51170dc346986eccefdc2dd33a9da36ead9`. The file is
`qwen2.5-1.5b-instruct-q4_k_m.gguf`, 1,117,320,736 bytes, SHA-256
`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`.
The retained model card declares Apache-2.0.

The [official llama.cpp Windows CPU release](https://github.com/ggml-org/llama.cpp/releases/tag/b11451)
is b11451. Its archive is `llama-b11451-bin-win-cpu-x64.zip`,
19,415,775 bytes, SHA-256
`77f0bdb1de3b48a8f6d671ebf79659ccfbe58d21e955319939ad07776d95296b`.
The extracted `llama-server.exe` has SHA-256
`aecb16de7c76ccfd11d34ce405d003b4a02f95602d9475d3b04a533d9708e087`.
The executable reports 0.6.0-dev, build 11451, commit 2207c8e57; its license is MIT.

## Effective configuration

Use loopback only, two CPU threads, one slot and an 8192-token context.
The observed executable supports the following arguments, with an absolute
verified model path supplied as `MODEL_PATH`:

```console
llama-server.exe --model MODEL_PATH --alias ragelit-qwen25-15b-q4km --host 127.0.0.1 --port 18080 --threads 2 --threads-batch 2 --threads-http 2 --parallel 1 --ctx-size 8192 --n-gpu-layers 0 --load-mode none --offline --no-webui --log-disable
```

Start only an owned hidden process in a confirmed awake window. Clear inherited
`LLAMA_*` overrides in its launch environment, not in user or machine settings.
The existing `CompatibleProvider` keeps temperature 0, max_tokens 1024, JSON
response format and a 30-second deadline. Do not raise that deadline after
observing a timeout. No API key or paid provider is used.

A declared checksum is not loaded-model proof. The owned process was checked
against its PID, start time and executable path. GET `/props` matched the verified
model path, build, context and slot count; GET `/v1/models` matched the alias.
A read-only file handle blocked writes to the model during the run, verified
with a denied write-open probe. This is local observation, not cryptographic
attestation. The owned server is stopped when the approved awake window ends.

## Initial smoke

The direct provider call took 16.0 seconds and returned the expected label and
citation. A separate production-path smoke seeded three selected documents:
public, group-restricted and user-restricted. All used real [pinned embeddings](model-pins.md)
and the local generation server through the application's ordinary chat path.
Two queries answered correctly; the third timed out at the unchanged deadline.
The whole smoke took 121.3 seconds, including 40.6 seconds of setup and seeding.

These three queries are a subset, not the 87-query benchmark. Raw observations,
source provenance and loaded-model proof are retained in ignored local storage.
No full-cohort Recall@10, security rate or release claim follows from this smoke.
