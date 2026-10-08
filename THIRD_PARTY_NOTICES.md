# Third-party notices

## Benchmark embedding assets

Benchmark weights are downloaded separately, not copied into this repository.
The committed pin manifest records the file hashes and source revisions:

- [Qdrant BGE ONNX model](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/aa8f8b060edb00e03bfdd08813a2949946c8ba55),
  revision `aa8f8b060edb00e03bfdd08813a2949946c8ba55`, declares MIT.
- [Qdrant BM25 files](https://huggingface.co/Qdrant/bm25/tree/22b8d2af71a76161e18dd432d2cee0eefa66e412),
  revision `22b8d2af71a76161e18dd432d2cee0eefa66e412`, declares Apache-2.0.

The asset folder retains each pinned model card. Preserve applicable notices
when redistributing assets. FastEmbed remains an existing locked dependency;
its code was not copied. See [local model pins](docs/evaluation/model-pins.md).

## Full Stack FastAPI Template

RAGelit adapts project configuration and layout conventions from the Full
Stack FastAPI Template:

- Source: https://github.com/fastapi/full-stack-fastapi-template
- Commit: `cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7`
- License: MIT
- Adapted files: `.gitignore`, `compose.yml`, `backend/pyproject.toml`,
  `frontend/biome.json`, `frontend/package.json`, `frontend/tsconfig.json`,
  and `frontend/vite.config.ts`

The adapted files have been reduced and changed for RAGelit. The original
license follows.

```text
MIT License

Copyright (c) 2019 Sebastián Ramírez

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
