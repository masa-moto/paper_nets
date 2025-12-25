# async_paper_graph.py
import asyncio
import aiohttp
import time
from collections import deque
import networkx as nx
import json
from typing import Dict, Any, Optional
import re 

# ---------- 設定 ----------
CROSSREF_BASE = "https://api.crossref.org/works/"
OPENCIT_BASE = "https://opencitations.net/index/coci/api/v1/citations/"

# 並列制御（同時接続数）
DEFAULT_CONCURRENCY = 5
# タイムアウト（秒）
REQUEST_TIMEOUT = 15
# リトライ回数
MAX_RETRIES = 2
# キャッシュ（メモリ内）
global meta_cache
meta_cache: Dict[str, Any] = {}
global cites_cache
cites_cache: Dict[str, Any] = {}
# in-flight map: 他コルーチンが既に取得中ならそれを await する
meta_inflight: Dict[str, asyncio.Task] = {}
cites_inflight: Dict[str, asyncio.Task] = {}


# ---------- ユーティリティ: 永続キャッシュ読み書き ----------
def save_cache(path_meta="meta_cache.json", path_cites="cites_cache.json"):
    with open(path_meta, "w", encoding="utf-8") as f:
        json.dump(meta_cache, f, ensure_ascii=False, indent=2)
    with open(path_cites, "w", encoding="utf-8") as f:
        json.dump(cites_cache, f, ensure_ascii=False, indent=2)


def load_cache(path_meta="meta_cache.json", path_cites="cites_cache.json"):
    try:
        with open(path_meta, "r", encoding="utf-8") as f:
            meta_cache.update(json.load(f))
    except FileNotFoundError:
        pass
    try:
        with open(path_cites, "r", encoding="utf-8") as f:
            cites_cache.update(json.load(f))
    except FileNotFoundError:
        pass

def load_graph(path_graph:str="graph.json"):
    from networkx.readwrite import json_graph
    with open(path_graph, "r", encoding="utf-8") as f:
        data = json.load(f)
    return json_graph.node_link_graph(data, edges = "edges")

def _slugify_title(title: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s[:max_len] or "untitled")

def _extract_ref_title_authors_year(ref: dict):
    title = ref.get("article-title") or ref.get("title") or ref.get("unstructured") or ref.get("journal-title")
    if isinstance(title, list):
        title = title[0] if title else None
    if title:
        title = title.strip()

    raw_author = ref.get("author") or ""
    if isinstance(raw_author, list):
        raw_author = ";".join([str(a) for a in raw_author])
    authors = [p.strip() for p in re.split(r";|,|&| and ", str(raw_author)) if p.strip()]

    year = ref.get("year") or ref.get("year-suffix")
    try:
        year = int(year) if year else None
    except Exception:
        year = None
    if year is None and isinstance(ref.get("unstructured"), str):
        m = re.search(r"(19|20)\d{2}", ref["unstructured"])
        year = int(m.group(0)) if m else None
    return title, authors, year


# ---------- ネットワークリクエスト（内部） ----------
async def _http_get_json(session: aiohttp.ClientSession, url: str, timeout_s=REQUEST_TIMEOUT):
    """単純なGET with timeout -> json。例外は上位で扱う。"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                # 非200は None を返して上位で扱う
                # ここでStatusコードログを出すのは有用
                # print(f"[WARN] HTTP {resp.status} for {url}")
                return None
    except Exception as e:
        print(f"[ERROR] request failed {url} : {e}")
        return None


# ---------- fetch with cache + inflight (metadata) ----------
async def fetch_metadata_async(session: aiohttp.ClientSession, doi: str,
                               sem: asyncio.Semaphore, retries=MAX_RETRIES) -> Optional[dict]:
    doi_lower = doi.lower()
    # キャッシュヒット->fetch data from meta_cache
    if doi_lower in meta_cache:
        return meta_cache[doi_lower]

    # すでに取得中のtaskがあれば await それを返す
    if doi_lower in meta_inflight:
        return await meta_inflight[doi_lower]

    # 新規fetch用の task を作る（inflight 管理）
    async def _do_fetch():
        backoff = 0.5
        for attempt in range(retries + 1):
            async with sem:
                url = CROSSREF_BASE + doi_lower
                data = await _http_get_json(session, url)
            if data is not None:
                # Crossrefは {"message": {...}} という形式で返す
                meta_cache[doi_lower] = data
                return data
            else:
                # 簡単な待ち（指数バックオフ）
                await asyncio.sleep(backoff)
                backoff *= 2.1
        return None

    task = asyncio.create_task(_do_fetch())
    meta_inflight[doi_lower] = task
    try:
        res = await task
        return res
    finally:
        meta_inflight.pop(doi_lower, None)


# ---------- fetch citations (opencitations) with cache + inflight ----------
async def fetch_citations_async(session: aiohttp.ClientSession, doi: str,
                                sem: asyncio.Semaphore, max_citations: int = 50,
                                retries=MAX_RETRIES) -> list:
    doi_lower = doi.lower()
    if doi_lower in cites_cache:
        return cites_cache[doi_lower]

    if doi_lower in cites_inflight:
        return await cites_inflight[doi_lower]

    async def _do_fetch():
        backoff = 0.5
        for attempt in range(retries + 1):
            async with sem:
                url = OPENCIT_BASE + doi_lower
                data = await _http_get_json(session, url)
            if data is not None:
                # data は list of dict 各要素に "citing" 等がある
                result = [d.get("citing") for d in data if "citing" in d][:max_citations]
                cites_cache[doi_lower] = result
                print(f"[INFO]:{url}")
                return result
            else:
                await asyncio.sleep(backoff)
                backoff *= 2
        return []

    task = asyncio.create_task(_do_fetch())
    cites_inflight[doi_lower] = task
    try:
        res = await task
        return res
    finally:
        cites_inflight.pop(doi_lower, None)


# ---------- BFSで並列に取得してグラフ構築 ----------
async def bfs_build_graph_async(start_doi: str,
                                max_depth: int = 2,
                                concurrency: int = DEFAULT_CONCURRENCY,
                                max_per_node_refs: int = 10,
                                max_per_node_cites: int = 10,
                                max_total_nodes: int = 1500,
                                G:nx.DiGraph = None) -> nx.DiGraph:
    """
    BFSで各レベルを並列取得して NetworkX の DiGraph を返す。
    各ノードのメタ情報は meta_cache に格納される（DOI -> response json）。
    """
    if G is None:
        G = nx.DiGraph()
    sem = asyncio.Semaphore(concurrency)

    # session は接続再利用のために1つだけ作る
    headers = {"User-Agent": "PaperNets/1.0 (mailto:example_sample@hotmail.com)"}
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        # queue に (doi, depth, from_doi, role_from_parent)
        q = deque()
        q.append((start_doi.lower(), 0, None, "input"))
        seen = set()  # 探索で二重に enqueue されるの防止
        seen.add(start_doi.lower())

        while q and len(G.nodes) < max_total_nodes:
            # 今のレベルの要素を取り出して並列実行用リストを作る
            batch = []
            # 制御：一度に処理する数を上限にする（queue が膨らむのを抑える）
            batch_size = min(len(q), concurrency * 10)
            for _ in range(batch_size):
                batch.append(q.popleft())

            # 並列に metadata を取る
            tasks = []
            for doi, depth, from_doi, role in batch:
                fetch_task = None if doi.startswith("title:") else asyncio.create_task(fetch_metadata_async(session, doi, sem))
                tasks.append((doi, depth, from_doi, role, fetch_task))

            # await all metadata
            for doi, depth, from_doi, role, task in tasks:
                # メタが取れない場合もある -> 最低ノードは作る
                meta_msg = None
                resp = await task if task else None
                if resp and isinstance(resp, dict):
                    # Crossref returns {"message": {...}}
                    meta_msg = resp.get("message", {})
                # fallback: if fetch failed but cache has a placeholder (e.g., title:...)
                if not meta_msg and doi in meta_cache:
                    cached = meta_cache.get(doi, {})
                    if isinstance(cached, dict):
                        meta_msg = cached.get("message", {})
                # ノード追加（role/from_doi をノード属性として残す）
                if doi not in G:
                    # 抜き出し：title, authors, year, journal
                    title = None
                    authors = []
                    year = None
                    journal = ""
                    if meta_msg:
                        title_list = meta_msg.get("title", [])
                        title = title_list[0] if title_list else meta_msg.get("title")
                        if "author" in meta_msg:
                            authors = [a.get("family", "") or a.get("name", "") for a in meta_msg.get("author", [])]
                        year = meta_msg.get("issued", {}).get("date-parts", [[None]])[0][0]
                        container = meta_msg.get("container-title", [])
                        journal = container[0] if container else ""
                    # derive a readable title for placeholder nodes if still missing
                    if (not title or title == "Unknown") and doi.startswith("title:"):
                        title = doi.replace("title:", "").replace("-", " ")
                    G.add_node(doi, doi=doi, title=title or "Unknown",
                        authors=authors, year=year, journal=journal,
                        roles=[(role, from_doi)])
                    if role == "input":
                        G.nodes[doi]["highlight"] = True

                else:
                    # 既存ノードには roles をマージ（重複可）
                    roles = G.nodes[doi].get("roles", [])
                    roles.append((role, from_doi))
                    G.nodes[doi]["roles"] = roles
                    if role == "input":
                        G.nodes[doi]["highlight"] = True
                        

                # エッジは parent->child (reference) or child->parent (citation)
                if (role == "reference" and from_doi) and not G.has_edge(from_doi, doi):
                    G.add_edge(from_doi, doi)
                elif (role == "citation" and from_doi) and not G.has_edge(doi, from_doi):
                    G.add_edge(doi, from_doi)
                elif role == "input":
                    pass

                # 次のレベル拡張（depth+1 が max_depth より小さい場合のみ）
                if depth + 1 <= max_depth and len(G.nodes) < max_total_nodes:
                    # references (Crossref metadata)
                    if meta_msg:
                        refs = meta_msg.get("reference", [])[:max_per_node_refs]
                        for r in refs:
                            if "DOI" in r:
                                child = r["DOI"].lower()
                                if child not in seen:
                                    seen.add(child)
                                    q.append((child, depth + 1, doi, "reference"))
                            else:
                                title, authors_ref, year_ref = _extract_ref_title_authors_year(r)
                                if not title:
                                    continue
                                child = f"title:{_slugify_title(title)}"
                                if year_ref:
                                    child = f"{child}-{year_ref}"
                                if child not in meta_cache:
                                    meta_cache[child] = {
                                        "message": {
                                            "title": [title],
                                            "author": [{"family": a} for a in authors_ref] if authors_ref else [],
                                            "issued": {"date-parts": [[year_ref]]} if year_ref else {},
                                            "container-title": []
                                        }
                                    }
                                if child not in seen:
                                    seen.add(child)
                                    q.append((child, depth + 1, doi, "reference"))
                    # citations (OpenCitations)
                    # ここは並列で取りたい -> enqueue a task that will fetch cites when popped
                    # we can prefetch citations in parallel here as well by scheduling tasks,
                    # but to keep BFS logic simple, queue child placeholders and fetch on processing
                    # -> Instead, prefetch cites async now:
                    # create a task to fetch citations and enqueue results immediately (tradeoff)
                    cites = await fetch_citations_async(session, doi, sem, max_citations=max_per_node_cites)
                    for child in cites:
                        if child:
                            child = child.lower()
                            if child not in seen:
                                seen.add(child)
                                q.append((child, depth + 1, doi, "citation"))

        return G


def visualize_graph(G, output_html = "graph.html"):
    # net = Network(notebook=False, cdn_resources="remote", directed=True)
    # for node, data in G.nodes(data=True):
    #     label = f"{data.get('authors', [''])[0]} et al., {data.get('year', '')}"
    #     color = "yellow" if data.get("highlight") else "lightblue"
    #     net.add_node(node, label=label, title=data.get("title", ""), color=color)
    # for u, v in G.edges():
    #     net.add_edge(u, v)
    # net.write_html(output_html)
    visualize_with_sidebar(G, output_html)

from jinja2 import Template
from pyvis.network import Network

def visualize_with_sidebar(G, output_html = "graph.html"):
    net = Network(notebook=False, cdn_resources="remote", directed=True)
    for node, data in G.nodes(data=True):
        if data.get("authors") and len(data["authors"])>1:
            first_author = data["authors"][0]+" et, al."
        elif data.get("authors") and len(data["authors"])>0:
            first_author = data["authors"][0]
        else:
            first_author = "Unknown"
        label = f"{first_author}, {data.get('year', '')}"
        color = "green" if data.get("highlight") else "lightblue"
        title = f"{data.get('title', '')} "#<br> Role: {data.get('role', '')}"
        net.add_node(node, label=label, title=title, color=color)
    for u, v in G.edges():
        net.add_edge(u, v)

    graph_html = net.generate_html()

    template = """
    <html>
    <head>
      <meta charset="utf-8">
      <title>Paper Network</title>
    </head>
    <body>
    <div style="display:flex; height:200vh;">
      <div style="flex:2; border-right:1px solid #ccc;">
        {{ graph_html|safe }}
      </div>
      <div style="flex:1; padding:1em; overflow-y:scroll;">
        <h2>References</h2>
        <ul>
        {% for doi, data in nodes %}
          <li>
                        {% set doi_val = data.get('doi') or doi %}
                        {% if doi_val and doi_val.startswith('10.') %}
                            <b>{{ data.title }}</b> ({{ data.year }})<br>
                            {{ data.authors|join(", ") }}<br>
                            <a href="https://doi.org/{{ doi_val }}">{{ doi_val }}</a>
                        {% else %}
                            <b>{{ data.title or doi }}</b>
                        {% endif %}
          </li>
        {% endfor %}
        </ul>
      </div>
    </div>
    </body>
    </html>
    """
    html = Template(template).render(graph_html=graph_html, nodes=G.nodes(data=True))
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html)
        
def save_as_yaml(G, output_yaml):
    import yaml
    data = {
        "nodes": [
            {"id": n, **d} for n, d in G.nodes(data=True)
        ],
        "edges": [
            {"source": u, "target": v} for u, v in G.edges()
        ]
    }
    with open(output_yaml, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
        
def save_as_json(G, output_json):
    data = {
        "nodes": [
            {"id": n, **d} for n, d in G.nodes(data=True)
        ],
        "edges": [
            {"source": u, "target": v} for u, v in G.edges()
        ]
    }
    with open(output_json, "w") as f:
        json.dump(data, f, indent=2)

def node_to_bibtex(node_id, data):
    """ノードのメタデータ(dict)を BibTeX エントリに変換"""
    key = node_id.replace("/", "_")  # doiをキーにするが記号を回避
    authors = " and ".join(data.get("authors", [])) if data.get("authors") else "Unknown"
    title = data.get("title", "Unknown Title")
    journal = data.get("journal", "Unknown Journal")
    year = data.get("year", "????")
    doi = data.get("doi", node_id)

    return f"""@article{{{key},
  title   = {{{title}}},
  author  = {{{authors}}},
  journal = {{{journal}}},
  year    = {{{year}}},
  doi     = {{{doi}}},
}}"""

def export_bibtex(G: nx.DiGraph, out_file: str):
    """グラフのノードから BibTeX ファイルを作成"""
    entries = []
    for node_id, data in G.nodes(data=True):
        entries.append(node_to_bibtex(node_id, data))
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n\n".join(entries))
    print(f"[INFO] BibTeX exported to {out_file}")

# ---------- 実例実行 ----------
import argparse
import os 

async def async_main_example():
    parser = argparse.ArgumentParser(description="文献ネットワーク可視化ツール")
    parser.add_argument("--doi", required=True, help="起点となるDOI")
    parser.add_argument("--concurrency", default = 5, help = "最大非同期処理プロセス数. default:5")
    parser.add_argument("--max_deg", default = 10, type = int,help = "文献当たりの cite/ref 最大探索文献数. default:5")
    parser.add_argument("--depth", type=int, default=1, help="探索の深さ default:1")
    parser.add_argument("--html", default="graph.html", help="出力HTMLファイル名")
    parser.add_argument("--json", default="graph.json", help="出力JSONファイル名")
    parser.add_argument("--yaml", default="graph.yaml", help="出力YAMLファイル名")
    parser.add_argument("--resume", default=None, help="入力JSONファイル名 (グラフ構造)")
    parser.add_argument("--meta", default="meta.json", help="入力JSONファイル名 (meta情報)")
    parser.add_argument("--cites", default="cites.json", help="入力JSONファイル名 (cites情報)")
    parser.add_argument("--bibtex-out", default=None, help="出力bibtexファイル名")
    args = parser.parse_args()
    if args.cites and args.meta:
        load_cache(path_cites=args.cites, path_meta=args.meta)  # 必要なら前回のキャッシュを読み込む
    if args.resume and os.path.exists(args.resume):
        print(f"[INFO] Resuming graph from {args.resume}")
        G =load_graph(args.resume)
    else:
        G = nx.DiGraph()
    print(f"[INFO] DOI = {args.doi}, depth = {args.depth}")
    start = time.time()
    G = await bfs_build_graph_async(f"{args.doi}",
                                    max_depth=args.depth,
                                    concurrency=args.concurrency,
                                    max_per_node_refs=args.max_deg,
                                    max_per_node_cites=args.max_deg,
                                    G = G)
    print(f"[INFO] Graph informations nodes:{len(G.nodes)}, edges:{len(G.edges)}")
    save_cache(path_meta= args.meta, path_cites= args.cites)  # 必要なら保存
    save_as_yaml(G, output_yaml=args.yaml)
    save_as_json(G, output_json=args.json)
    if args.bibtex_out:
        export_bibtex(G, args.bibtex_out)
            
    visualize_graph(G, output_html="graph.html")
    
    print("[INFO] Elapsed time:", time.time() - start)
    


if __name__ == "__main__":
    asyncio.run(async_main_example())
