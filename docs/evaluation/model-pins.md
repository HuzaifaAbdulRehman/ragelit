# Local embedding pins

The benchmark uses the production `FastEmbedProvider` document/query methods
with verified local assets. Its loader is `PinnedEmbeddingProvider`; it does
not change the ordinary application's model-loading defaults.

`backend/app/evaluation/embedding-pins.json` records immutable source revisions,
byte sizes and SHA-256 for eleven files. FastEmbed is fixed to `0.8.1`.

| Component | Effective source | Revision | Declared license |
| --- | --- | --- | --- |
| Dense BGE, 384 dimensions | [Qdrant ONNX model](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/aa8f8b060edb00e03bfdd08813a2949946c8ba55) | `aa8f8b060edb00e03bfdd08813a2949946c8ba55` | MIT |
| Sparse BM25, English | [Qdrant BM25 files](https://huggingface.co/Qdrant/bm25/tree/22b8d2af71a76161e18dd432d2cee0eefa66e412) | `22b8d2af71a76161e18dd432d2cee0eefa66e412` | Apache-2.0 |

Dense settings are two threads and `CPUExecutionProvider`. Sparse settings are
two threads, English, stemming enabled, `k=1.2`, `b=0.75`, `avg_len=256`, and
maximum token length 40. Qdrant must use sparse IDF, as the production store
does. The default fingerprint is
`8d52f1a1b08773e9716127c8394c9fb7cd424b9d018a682e95c5afe9bb921552`.
It includes pins and these settings. It does not include a
machine path, claim repeatability across hardware, or attest to a generation
server's loaded weights.

## Download without authentication

From the repository root in PowerShell, choose a new folder below ignored
`.superpowers`. The commands refuse to overwrite an existing folder. Public
HTTPS downloads retain both model cards; about 67 MB is required. No model code
is executed during download, and weights are not committed.

```powershell
$assetRoot = Join-Path (Get-Location) '.superpowers/benchmark-models-v1'
if (Test-Path -LiteralPath $assetRoot) { throw 'Choose a new asset folder.' }
$pins = Get-Content backend/app/evaluation/embedding-pins.json -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Path $assetRoot | Out-Null
foreach ($kind in @('dense', 'sparse')) {
    $pin = $pins.$kind
    $folder = Join-Path $assetRoot $kind
    New-Item -ItemType Directory -Path $folder | Out-Null
    foreach ($file in $pin.files) {
        $target = Join-Path $folder $file.name
        $uri = "https://huggingface.co/$($pin.repository)/resolve/$($pin.revision)/$($file.name)"
        Invoke-WebRequest -Uri $uri -OutFile $target -UseBasicParsing -TimeoutSec 90
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ((Get-Item -LiteralPath $target).Length -ne $file.size -or $hash -ne $file.sha256) {
            throw 'Downloaded file does not match its pin.'
        }
    }
}
```

Leave an interrupted folder intact and use a fresh name for another attempt.
The loader requires exactly `dense` and `sparse`, each containing its declared
files; missing, extra, changed or linked assets reject before model construction.

From `backend`, verify and print the fingerprint with an absolute asset path:

```console
uv run --frozen python -c "from pathlib import Path; from app.evaluation.models import verify_embedding_assets; print(verify_embedding_assets(Path('D:/replace/with/your/asset/folder')).fingerprint)"
```

Construct `PinnedEmbeddingProvider` with that same path. It loads explicit local
paths with `local_files_only=True`; it has no network fallback. Document/query
embedding smoke tests establish loading and vector construction, not Recall@10,
generation quality, tenant isolation, or a completed application benchmark.

The loader verifies bytes before loading from this owned local folder. Keep the
folder unchanged during a run; hashes are not signatures or protection against
a concurrent process rewriting the files. Generation pins, raw query artifacts,
all original primary measurements, and release gates remain separate work.
