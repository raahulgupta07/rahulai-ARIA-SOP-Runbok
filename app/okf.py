"""OKF (Open Knowledge Format) export — package Aria's brain as a vendor-neutral
markdown bundle.

OKF (https://github.com/GoogleCloudPlatform/knowledge-catalog) is a tiny open
spec: a directory of markdown files with YAML frontmatter. Only `type` is
required; links are plain markdown; `index.md` lists a directory and `log.md`
records history. Aria's compiled wiki + facts + ingest log already match this
shape, so export is a READ-ONLY walk over existing tables — no writes to the
live system, nothing new computed.

The result is a conformant OKF v0.1 bundle streamed as a gzip tarball that opens
in any editor, renders on GitHub, and re-imports anywhere. No vendor lock-in.
"""
import io
import re
import tarfile
import datetime as dt

from .db import get_conn

OKF_VERSION = "0.1"


# ---- helpers --------------------------------------------------------------

def _slug(name: str, used: set) -> str:
    """kebab-case, de-duplicated, capped — becomes the concept's file path."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "untitled").lower()).strip("-")
    s = (s or "untitled")[:60]
    base, n = s, 2
    while s in used:
        s = f"{base}-{n}"
        n += 1
    used.add(s)
    return s


def _yaml(d: dict) -> str:
    """Minimal frontmatter writer: flat scalars + list values (e.g. tags)."""
    lines = ["---"]
    for k, v in d.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            inner = ", ".join(str(x) for x in v if x is not None and x != "")
            if not inner:
                continue
            lines.append(f"{k}: [{inner}]")
        else:
            s = str(v).replace("\n", " ").strip()
            if re.search(r":\s|[#\[\]{}]", s) or s[:1] in "\"'>|*&!%@`":
                s = '"' + s.replace('"', '\\"') + '"'
            lines.append(f"{k}: {s}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _humanize(raw: str) -> str:
    """Port of frontend docname.parseDocName: strip extension + SOP code prefix,
    underscores -> spaces. Keeps the bundle's titles readable instead of raw
    `SOP_IT_..._.pdf` filenames when doc_wiki has no compiled title."""
    base = re.sub(r"\.(pdf|png|jpe?g)$", "", raw or "", flags=re.I)
    m = re.match(r"^(SOP[_-].*?)_([A-Z0-9]+)_(\d{2,4})_(.+)$", base)
    if m:
        title = m.group(4)
    else:
        m2 = re.search(r"_(\d{2,4})_(.+)$", base)
        title = m2.group(2) if m2 else base
    return re.sub(r"[_]+", " ", title).strip() or base


def _iso(ts) -> str | None:
    if not ts:
        return None
    try:
        return ts.isoformat()
    except Exception:
        return str(ts)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---- read the brain (read-only) -------------------------------------------

def _fetch():
    with get_conn() as conn:
        docs = conn.execute(
            "SELECT d.id, d.name, d.lang, d.page_count, d.created_at, d.ready_at, "
            "(SELECT p.id FROM pages p WHERE p.doc_id=d.id "
            " ORDER BY p.page_no, p.id LIMIT 1) AS cover_page_id "
            "FROM docs d WHERE d.status='ready' ORDER BY d.id"
        ).fetchall()
        wiki = {r["doc_id"]: r for r in conn.execute(
            "SELECT doc_id, title, summary, md FROM doc_wiki").fetchall()}
        pages_md: dict = {}
        for r in conn.execute(
            "SELECT doc_id, page_no, md FROM doc_pages_md "
            "ORDER BY doc_id, page_no").fetchall():
            pages_md.setdefault(r["doc_id"], []).append(r)
        # only ACTIVE facts leave the building (pending/rejected stay internal)
        facts = conn.execute(
            "SELECT id, key, value, source, created_at, last_cited_at, cited_count "
            "FROM memory WHERE status='active' ORDER BY id").fetchall()
        cites: dict = {}
        for r in conn.execute(
            "SELECT fact_id, question, at FROM fact_citation "
            "ORDER BY at DESC").fetchall():
            cites.setdefault(r["fact_id"], []).append(r)
        log = conn.execute(
            "SELECT ts, step, msg FROM ingest_log ORDER BY ts DESC LIMIT 500"
        ).fetchall()
    return docs, wiki, pages_md, facts, cites, log


# ---- bundle assembly ------------------------------------------------------

def _related_map(ids: list[int]) -> dict:
    """doc_id -> related doc_ids, from co-citation (pages of two docs cited in the
    same answer) + explicit doc_links. Lets the export carry a real graph."""
    if not ids:
        return {}
    keep = set(ids)
    rel: dict = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.doc_id AS d1, b.doc_id AS d2, count(*) AS n "
            "FROM messages m "
            "JOIN jsonb_array_elements(m.pages) ea ON true "
            "JOIN pages a ON a.id = (ea->>'page_id')::int "
            "JOIN jsonb_array_elements(m.pages) eb ON true "
            "JOIN pages b ON b.id = (eb->>'page_id')::int "
            "WHERE m.role='bot' AND a.doc_id < b.doc_id "
            "GROUP BY a.doc_id, b.doc_id ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            rel.setdefault(r["d1"], []).append(r["d2"])
            rel.setdefault(r["d2"], []).append(r["d1"])
        for r in conn.execute("SELECT src_doc, dst_doc FROM doc_links").fetchall():
            rel.setdefault(r["src_doc"], []).append(r["dst_doc"])
            rel.setdefault(r["dst_doc"], []).append(r["src_doc"])
    out: dict = {}
    for d, lst in rel.items():
        seen: list = []
        for x in lst:
            if x in keep and x != d and x not in seen:
                seen.append(x)
        if seen:
            out[d] = seen[:6]
    return out


def build_files() -> list[tuple[str, str]]:
    """Assemble the full OKF tree as a list of (path, text)."""
    docs, wiki, pages_md, facts, cites, log = _fetch()
    files: list[tuple[str, str]] = []

    # pre-assign doc slugs first so cross-links can resolve
    used_doc: set = set()
    doc_slug: dict = {}
    doc_title: dict = {}
    for d in docs:
        w = wiki.get(d["id"]) or {}
        # humanize whichever we pick — the compiled wiki title is often still the
        # raw `SOP_..._.pdf` filename; a clean title passes through unchanged.
        title = (_humanize(w.get("title") or d["name"]) or f"Document {d['id']}").strip()
        doc_title[d["id"]] = title
        doc_slug[d["id"]] = _slug(title, used_doc)

    related = _related_map([d["id"] for d in docs])

    # --- document concepts ---
    doc_index = ["# Documents\n"]
    for d in docs:
        w = wiki.get(d["id"]) or {}
        slug, title = doc_slug[d["id"]], doc_title[d["id"]]
        summary = (w.get("summary") or "").strip()
        body = (w.get("md") or "").strip()
        if not body:
            body = "\n\n".join((p["md"] or "").strip()
                               for p in pages_md.get(d["id"], [])).strip()
        if not summary and body:
            summary = re.sub(r"\s+", " ", re.sub(r"[#*`|]", "", body))[:160].strip()
        fm = {
            "type": "SOP",
            "title": title,
            "description": summary,
            "resource": f"/pages/{d['cover_page_id']}" if d.get("cover_page_id") else None,
            "tags": [t for t in [d.get("lang"), "sop", "runbook"] if t],
            "timestamp": _iso(d.get("ready_at") or d.get("created_at")),
            "pages": d.get("page_count") or None,
        }
        out = _yaml(fm)
        out += (body.rstrip() + "\n") if body else f"# {title}\n\n_No compiled content._\n"
        rels = related.get(d["id"]) or []
        if rels:
            out += "\n# Related\n\n"
            for rid in rels:
                if doc_slug.get(rid):
                    out += f"* [{doc_title[rid]}](/documents/{doc_slug[rid]}.md)\n"
        files.append((f"documents/{slug}.md", out))
        doc_index.append(f"* [{title}](/documents/{slug}.md)"
                         + (f" - {summary}" if summary else ""))
    files.append(("documents/index.md", "\n".join(doc_index) + "\n"))

    # --- fact concepts ---
    used_fact: set = set()
    fact_index = ["# Facts\n"]
    for f in facts:
        raw = (f.get("key") or f.get("value") or f"fact-{f['id']}").strip()
        title = re.sub(r"\s+", " ", raw)[:80].strip()
        slug = _slug(title, used_fact)
        fm = {
            "type": "Fact",
            "title": title,
            "tags": ["fact", f.get("source") or "human"],
            "timestamp": _iso(f.get("last_cited_at") or f.get("created_at")),
            "cited_count": f.get("cited_count") or 0,
        }
        out = _yaml(fm) + (f.get("value") or "").strip() + "\n"
        fc = cites.get(f["id"]) or []
        if fc:
            out += "\n# Citations\n\n"
            for i, c in enumerate(fc[:10], 1):
                q = re.sub(r"\s+", " ", (c.get("question") or "answer")).strip()[:100]
                out += f"[{i}] {q} ({_iso(c.get('at'))})\n"
        files.append((f"facts/{slug}.md", out))
        fact_index.append(f"* [{title}](/facts/{slug}.md)")
    if facts:
        files.append(("facts/index.md", "\n".join(fact_index) + "\n"))

    # --- root index.md (ONLY place okf_version frontmatter is allowed, per §11) ---
    root = _yaml({
        "okf_version": OKF_VERSION,
        "type": "Bundle",
        "title": "City Agent Aria — Knowledge Bundle",
        "timestamp": _now_iso(),
    })
    root += (
        "# City Agent Aria — Knowledge Bundle\n\n"
        "Exported in [Open Knowledge Format]"
        "(https://github.com/GoogleCloudPlatform/knowledge-catalog) "
        f"v{OKF_VERSION}. Plain markdown — open in any editor, render on GitHub, "
        "re-import anywhere.\n\n"
        f"* [Documents](/documents/index.md) - {len(docs)} runbooks / SOPs\n"
        f"* [Facts](/facts/index.md) - {len(facts)} curated facts\n"
    )
    files.append(("index.md", root))

    # --- log.md from the ingest activity feed ---
    files.append(("log.md", _build_log(log)))

    # --- self-contained graph viewer ---
    files.append(("viz.html", _viz_html(docs, doc_slug, doc_title, facts)))

    return files


def _build_log(rows) -> str:
    out = ["# Knowledge Update Log\n"]
    cur = None
    for r in rows:
        day = r["ts"].date().isoformat() if r.get("ts") else "undated"
        if day != cur:
            out.append(f"\n## {day}")
            cur = day
        step = (r.get("step") or "update").strip().replace("_", " ").capitalize()
        msg = re.sub(r"\s+", " ", (r.get("msg") or "")).strip()
        out.append(f"* **{step}**: {msg}")
    if cur is None:
        out.append("\n_No activity recorded._")
    return "\n".join(out) + "\n"


def _viz_html(docs, doc_slug, doc_title, facts) -> str:
    """A single self-contained file: ECharts force graph, data inlined. A center
    'Aria' hub with each document + fact as a spoke (sized by pages / citations).
    Opens with a double-click; the only network call is the ECharts CDN script."""
    import json
    nodes = [{"name": "City Agent Aria", "symbolSize": 46,
              "itemStyle": {"color": "#c2683f"}, "category": 0}]
    links = []
    for d in docs:
        nm = doc_title[d["id"]][:40]
        nodes.append({"name": nm, "category": 1,
                      "symbolSize": 12 + min(28, (d.get("page_count") or 1))})
        links.append({"source": "City Agent Aria", "target": nm})
    for f in facts[:120]:
        nm = re.sub(r"\s+", " ", (f.get("key") or f.get("value") or "fact")).strip()[:30]
        nodes.append({"name": nm, "category": 2,
                      "symbolSize": 8 + min(20, (f.get("cited_count") or 0) * 2)})
        links.append({"source": "City Agent Aria", "target": nm})
    cats = [{"name": "Bundle"}, {"name": "Documents"}, {"name": "Facts"}]
    data = json.dumps({"nodes": nodes, "links": links, "categories": cats})
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Aria Knowledge — OKF bundle</title>
<style>html,body{{margin:0;height:100%;background:#faf9f5;font-family:-apple-system,Segoe UI,Roboto,sans-serif}}
#h{{padding:14px 18px;color:#2b2a27}}#h b{{color:#c2683f}}#g{{height:calc(100% - 56px)}}</style>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script></head>
<body><div id="h"><b>City Agent Aria</b> — Open Knowledge Format bundle · {len(docs)} documents · {len(facts)} facts</div>
<div id="g"></div><script>
const D={data};
const c=echarts.init(document.getElementById('g'));
c.setOption({{tooltip:{{}},legend:[{{data:D.categories.map(x=>x.name),top:8,right:16}}],
color:['#c2683f','#3f7fb0','#2f8f83'],
series:[{{type:'graph',layout:'force',roam:true,label:{{show:true,position:'right',fontSize:10}},
force:{{repulsion:90,edgeLength:80}},data:D.nodes,links:D.links,categories:D.categories,
lineStyle:{{color:'#d8d3c8',curveness:0.1}}}}]}});
window.addEventListener('resize',()=>c.resize());
</script></body></html>
"""


# ---- public API -----------------------------------------------------------

def _doc_slugs() -> dict:
    """doc_id -> slug, recomputed identically to build_files (same order/dedup)."""
    docs, wiki, *_ = _fetch()
    used: set = set()
    slug: dict = {}
    for d in docs:
        w = wiki.get(d["id"]) or {}
        t = (_humanize(w.get("title") or d["name"]) or f"Document {d['id']}").strip()
        slug[d["id"]] = _slug(t, used)
    return slug


def build_image_files() -> list[tuple[str, bytes]]:
    """(path, bytes) for each page image, under images/<doc_slug>/<page_no>.png.
    Opt-in (export `images=1`) so the default bundle stays small."""
    from pathlib import Path
    slug = _doc_slugs()
    out: list[tuple[str, bytes]] = []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT doc_id, page_no, image_path FROM pages "
            "WHERE coalesce(image_path,'') <> '' ORDER BY doc_id, page_no"
        ).fetchall()
    for r in rows:
        s = slug.get(r["doc_id"])
        if not s:
            continue
        p = Path(r["image_path"])
        if not p.is_file():
            continue
        try:
            out.append((f"images/{s}/{r['page_no']}.png", p.read_bytes()))
        except Exception:
            pass
    return out


def build_tarball(include_images: bool = False) -> bytes:
    """Gzip tarball of the bundle, rooted at aria-okf/. With include_images, also
    bundles page images so a round-trip import keeps answer-with-page."""
    buf = io.BytesIO()
    mtime = int(dt.datetime.now(dt.timezone.utc).timestamp())
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def _add(path: str, data: bytes):
            info = tarfile.TarInfo(name=f"aria-okf/{path}")
            info.size = len(data)
            info.mtime = mtime
            tar.addfile(info, io.BytesIO(data))
        for path, text in build_files():
            _add(path, text.encode("utf-8"))
        if include_images:
            for path, data in build_image_files():
                _add(path, data)
    return buf.getvalue()


def preview() -> dict:
    """File listing + sizes for the admin UI (no download)."""
    files = build_files()
    items = [{"path": p, "bytes": len(t.encode("utf-8"))} for p, t in files]
    return {
        "files": items,
        "count": len(items),
        "total_bytes": sum(i["bytes"] for i in items),
        "documents": sum(1 for p, _ in files if p.startswith("documents/") and not p.endswith("index.md")),
        "facts": sum(1 for p, _ in files if p.startswith("facts/") and not p.endswith("index.md")),
        "okf_version": OKF_VERSION,
    }


# ===========================================================================
# IMPORT — feed an OKF bundle straight into the brain (skips the vision
# pipeline entirely: the markdown body IS the compiled wiki, so onboarding is
# instant and costs nothing). Conformant-permissive: unknown types/fields and
# broken links are tolerated, never fatal.
# ===========================================================================

RESERVED = {"index.md", "log.md"}


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Tolerant: no/broken frontmatter → ({}, text)."""
    import yaml
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, m.group(2)


def _pages_from_body(body: str) -> list[str]:
    """Split a concept body on our own `## Page N` markers into page chunks.
    A foreign bundle with no such markers becomes a single page."""
    parts = re.split(r"(?m)^##\s+Page\s+\d+\s*$", body)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks or [body.strip() or ""]


def _is_junk(base: str) -> bool:
    return base.startswith("._") or base == ".DS_Store"


def _iter_members(raw: bytes):
    """Yield (normalized_path, bytes) for every file in a .tar/.tar.gz/.zip bundle.
    Strips a single leading bundle-root dir and skips macOS junk."""
    if raw[:2] == b"PK":
        import zipfile
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:
            raise ValueError(f"not a readable zip archive: {e}")
        with zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                base = name.rsplit("/", 1)[-1]
                if _is_junk(base):
                    continue
                path = re.sub(r"^[^/]+/", "", name) if "/" in name else name
                yield path, zf.read(name)
        return
    try:
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except tarfile.TarError as e:
        raise ValueError(f"not a readable tar/tar.gz/zip archive: {e}")
    with tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            base = m.name.rsplit("/", 1)[-1]
            if _is_junk(base):
                continue
            f = tar.extractfile(m)
            if not f:
                continue
            path = re.sub(r"^[^/]+/", "", m.name) if "/" in m.name else m.name
            yield path, f.read()


def _read_archive(raw: bytes) -> list[tuple[str, str]]:
    """*.md members as (path, text)."""
    return [(p, data.decode("utf-8", "replace"))
            for p, data in _iter_members(raw) if p.lower().endswith(".md")]


def _read_images(raw: bytes) -> dict:
    """{doc_slug: {page_no: bytes}} from any images/<slug>/<page_no>.<ext> members."""
    imgs: dict = {}
    for p, data in _iter_members(raw):
        m = re.match(r"images/([^/]+)/(\d+)\.(png|jpe?g)$", p, re.I)
        if m:
            imgs.setdefault(m.group(1), {})[int(m.group(2))] = data
    return imgs


def fetch_url(url: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    """Download a bundle archive over HTTPS (size + scheme guarded). A GitHub
    repo URL is rewritten to its codeload tarball so `https://github.com/o/r`
    just works."""
    import urllib.request
    if not url.lower().startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git|/)?$", url.strip())
    if m:
        url = f"https://codeload.github.com/{m.group(1)}/{m.group(2)}/tar.gz/refs/heads/main"
    req = urllib.request.Request(url, headers={"User-Agent": "aria-okf"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("archive exceeds size limit")
    return data


def parse_bundle(raw: bytes) -> dict:
    """Validate + classify a bundle without writing anything (dry run)."""
    members = _read_archive(raw)
    documents, facts, skipped = [], [], []
    paths = {p for p, _ in members}
    for path, text in members:
        base = path.rsplit("/", 1)[-1]
        if base in RESERVED:
            continue
        fm, body = _split_frontmatter(text)
        ctype = str(fm.get("type") or "").strip()
        if not ctype:                       # conformance §9: type is required
            skipped.append({"path": path, "reason": "no type"})
            continue
        title = (fm.get("title") or base[:-3]).strip()
        if path.startswith("facts/") or ctype.lower() == "fact":
            facts.append({"path": path, "title": title, "fm": fm, "body": body})
        else:
            documents.append({"path": path, "title": title, "type": ctype,
                              "fm": fm, "body": body})
    # link audit: bundle-relative markdown links between concepts
    links = broken = 0
    for d in documents:
        for tgt in re.findall(r"\]\((/[^)]+\.md)\)", d["body"]):
            links += 1
            if tgt.lstrip("/") not in paths:
                broken += 1
    return {
        "documents": documents, "facts": facts, "skipped": skipped,
        "counts": {"documents": len(documents), "facts": len(facts),
                   "skipped": len(skipped), "links": links, "links_broken": broken},
    }


def import_bundle(raw: bytes, created_by: str | None = None) -> dict:
    """Commit a parsed bundle into the brain. Reuses the docs/pages/wiki tables
    directly (NO process_doc — that's the PDF/vision path). Imported facts land
    'pending' so they respect the review gate and don't override docs."""
    from pathlib import Path
    from .ingest import detect_lang, _now
    from .memory import add_memory
    from .db import log_ingest
    from .config import PAGES_DIR

    parsed = parse_bundle(raw)
    imgs = _read_images(raw)             # {doc_slug: {page_no: bytes}} if bundled
    made_docs = made_pages = made_facts = dup_docs = made_links = made_imgs = 0
    path_to_doc: dict[str, int] = {}     # concept path -> doc_id (for link resolution)

    with get_conn() as conn:
        for d in parsed["documents"]:
            title = d["title"]
            # dedupe by name so re-import is idempotent
            exists = conn.execute(
                "SELECT id FROM docs WHERE name = %s LIMIT 1", (title,)).fetchone()
            if exists:
                path_to_doc[d["path"]] = exists["id"]   # still resolvable as a link target
                dup_docs += 1
                continue
            fm, body = d["fm"], d["body"]
            tags = fm.get("tags") or []
            lang = next((t for t in tags if t in ("en", "my", "burmese")), None) \
                or detect_lang(body[:2000]) or "en"
            summary = (fm.get("description") or "").strip()
            pages = _pages_from_body(body)
            doc = conn.execute(
                "INSERT INTO docs (name, lang, page_count, status, progress, "
                "ready_at, created_at) VALUES (%s,%s,%s,'ready',100,%s, now()) "
                "RETURNING id", (title, lang, len(pages), _now())).fetchone()
            did = doc["id"]
            doc_imgs = imgs.get(d["path"].rsplit("/", 1)[-1][:-3], {})  # by slug
            for i, chunk in enumerate(pages, 1):
                # markdown-only unless the bundle shipped a page image for this page
                image_path = ""
                if i in doc_imgs:
                    ddir = PAGES_DIR / f"okf_{did}"
                    ddir.mkdir(parents=True, exist_ok=True)
                    fp = ddir / f"{i}.png"
                    try:
                        fp.write_bytes(doc_imgs[i])
                        image_path = str(fp)
                        made_imgs += 1
                    except Exception:
                        image_path = ""
                conn.execute(
                    "INSERT INTO pages (doc_id, page_no, image_path, text) "
                    "VALUES (%s,%s,%s,%s)", (did, i, image_path, chunk))
                conn.execute(
                    "INSERT INTO doc_pages_md (doc_id, page_no, md, chars) "
                    "VALUES (%s,%s,%s,%s)", (did, i, chunk, len(chunk)))
                conn.execute(
                    "INSERT INTO nodes (doc_id, page_no, title, summary) "
                    "VALUES (%s,%s,%s,%s)", (did, i, title, summary[:300]))
                made_pages += 1
            conn.execute(
                "INSERT INTO doc_wiki (doc_id, title, summary, md) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (doc_id) DO UPDATE "
                "SET title=EXCLUDED.title, summary=EXCLUDED.summary, md=EXCLUDED.md",
                (did, title, summary, body))
            path_to_doc[d["path"]] = did
            made_docs += 1
            log_ingest(did, "okf-import", f"📦 imported from OKF · {len(pages)} pages")

        # second pass: resolve cross-doc markdown links → persist real edges
        for d in parsed["documents"]:
            src = path_to_doc.get(d["path"])
            if not src:
                continue
            for tgt in re.findall(r"\]\((/[^)]+\.md)\)", d["body"]):
                dst = path_to_doc.get(tgt.lstrip("/"))
                if dst and dst != src:
                    conn.execute(
                        "INSERT INTO doc_links (src_doc, dst_doc, source) "
                        "VALUES (%s,%s,'okf') ON CONFLICT DO NOTHING", (src, dst))
                    made_links += 1

    # facts: pending review (never auto-active from an external bundle)
    for f in parsed["facts"]:
        val = (f["body"] or "").strip()
        if not val:
            continue
        add_memory(val, key=f["title"], source="okf",
                   created_by=created_by, status="pending")
        made_facts += 1

    log_ingest(None, "okf-import",
               f"📦 OKF import done · {made_docs} docs · {made_facts} facts (pending)")
    return {
        "imported_documents": made_docs,
        "imported_pages": made_pages,
        "imported_images": made_imgs,
        "imported_facts_pending": made_facts,
        "imported_links": made_links,
        "duplicate_documents_skipped": dup_docs,
        "skipped": parsed["counts"]["skipped"],
        "links": parsed["counts"]["links"],
        "links_broken": parsed["counts"]["links_broken"],
    }
