# Vendored: PageIndex Flash

- Upstream: https://github.com/VectifyAI/PageIndex (branch `main`, v0.2.20)
- Commit: `f279431eb4e47884862961b9718df180552f417a` (2026-09-30)
- Vendored: 2026-10-01
- License: MIT (see `LICENSE`, copied from upstream)

## What is here
| Path | Source | Modified? |
|---|---|---|
| `flash/**` | `pageindex/flash/**` | No (dropped `flash/assets/time_vs_pages.png` only) |
| `naming.py` | `pageindex/naming.py` | No (Flash's `api.py` imports `sanitize_filename`) |
| `utils.py` | `pageindex/utils.py` | **Shim** — only `write_node_id` + `strip_internal_keys`, verbatim |
| `__init__.py` | — | New (makes `flash_upstream` the parent package for Flash's `..` imports) |

Import as `from flash_upstream.flash.main import extract_toc` with
`vendor_pageindex/` on `sys.path` (`app/tree_flash.py` does this). The package
is named `flash_upstream`, not `pageindex`, so it never shadows the older
vendored `vendor_pageindex/pageindex/` LLM builder.

The LLM summary/optimize path (`summary=True` / `optimize=...`) is NOT usable
here: it needs upstream `utils.py` (litellm) and `tree_optimize.py`, which were
not vendored on purpose.

## Runtime deps
`pypdfium2`, `regex`, `sortedcontainers`, `PyPDF2` (pinned in `requirements.txt`).

## Updating
Re-clone upstream, replace `flash/` and `naming.py`, re-check that Flash still
only imports `..naming` and (lazily) `..utils.write_node_id/strip_internal_keys`
on the LLM-free path: `grep -rn "from \.\.\(utils\|naming\|tree_optimize\)" flash`.
