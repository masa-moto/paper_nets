import argparse
import asyncio
import aiohttp
from collections import deque
import json
import networkx as nx
import requests
from tqdm import tqdm
import time
from typing import Dict, Set, Any, OPtional
from pyvis.network import Network
import yaml

# -------------------------------
# 0. 準備
# -------------------------------

CACHE = {}  #既知のdoiについて、apiを触りにいかないように -> response_json
API_URL = "https://api.crossref.org/works/" #ここにアクセスして情報を取得する
DEFAULT_CONCURRENCY = 5
REQUEST_TIMEOUT = 10
MAX_RETIRES = 2

meta_cache: Dict[str, Any] = {}
cites_cache: Dict[str, Any] = {}
meta_inflight = Dict[str, asyncio.Task] = {}
cites_inflight = Dict[str, asyncio.Task] = {}

# -------------------------------
# 0.1 cache周り
# -------------------------------
def save_cache(path_meta = "meta_cache.json", path_cites = "cites_cache.json"):
    with open(path_meta, "w", encoding="utf-8") as f:
        json.dump(meta_cache, f, ensure_ascii=False, indent = 2)
    with open(path_cites, "w", encoding="utf-8") as f:
        json.dump(cites_caches, f, ensure_ascii=False, indent=2)
    
def load_cache(path_meta="meta_cache.json", path_cites = "cites_cache.json"):
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


# -------------------------------
# 1. Crossrefからメタ情報取得
# -------------------------------
def fetch_metadata(doi):
    url = f"https://api.crossref.org/works/{doi}"
    r = requests.get(url)
    if r.status_code != 200:
        return None
    item = r.json()["message"]
    journal_list = item.get("container-title", [])
    meta = {
        "doi": doi,
        "title": item.get("title", ["No Title"])[0],
        "authors": [a.get("family", "") for a in item.get("author", [])] if "author" in item else [],
        "year": item.get("issued", {}).get("date-parts", [[None]])[0][0],
        "journal": journal_list[0] if journal_list else "",#item.get("container-title", [""])[0],
        "url": item.get("URL", "")
    }
    return meta

# -------------------------------
# 2. 引用文献（references）
# -------------------------------
def fetch_references(doi, max_reference=5):
    url = f"https://api.crossref.org/works/{doi}"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    item = r.json()["message"]
    _list = []
    for ref in tqdm(item.get("reference", [])[:max_reference], ncols=25, leave = False):
        if "DOI" in ref:
            _list.append(ref.get("DOI"))
    # _list =  [ref.get("DOI") for ref in item.get("reference", []) if "DOI" in ref]
    return _list

# -------------------------------
# 3. 被引用文献（citations, OpenCitations API）
# -------------------------------
def fetch_citations(doi, max_citations=5):
    url = f"https://opencitations.net/index/coci/api/v1/citations/{doi}"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    _list = []
    print(f"Total citations avaiable: {len(data)}. Limiting to {max_citations}")
    for d in tqdm(data[:max_citations], desc="fetch citations", ncols=25, leave = False):
        _list.append(d["citing"])
    return _list  
    # return [d["citing"] for d in data]

# -------------------------------
# 4. グラフ構築
# -------------------------------
def build_graph(start_doi, depth=1):
    G = nx.DiGraph()
    visited = set()

    def add_node_with_metadata(doi, role = "input", from_doi=None, highlight=False):
        if doi not in G:
            meta = fetch_metadata(doi) or {"doi": doi, "title": "Unknown"}
            meta["highlight"] = highlight
            meta["role"] = role
            G.add_node(doi, **meta)

    def explore(doi, current_depth):
        if current_depth >= depth or doi in visited:
            return
        visited.add(doi)
        print(f"current depth : {current_depth}")
        # 引用文献（出ていくエッジ）
        if current_depth < depth:
            time.sleep(1)
            for ref_doi in fetch_references(doi):
                add_node_with_metadata(ref_doi, role="reference", from_doi = doi)
                G.add_edge(doi, ref_doi)
                explore(ref_doi, current_depth + 1)
                time.sleep(.5)
                
            # 被引用文献（入ってくるエッジ）
            time.sleep(1)
            for cit_doi in fetch_citations(doi):
                add_node_with_metadata(cit_doi, role = "citation", from_doi = doi)
                G.add_edge(cit_doi, doi)
                explore(cit_doi, current_depth + 1)
                time.sleep(.5)

    add_node_with_metadata(start_doi, role = "input", from_doi = None, highlight=True)
    explore(start_doi, 0)
    return G

# -------------------------------
# 5. 可視化
# -------------------------------
def visualize_graph(G, output_html):
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

def visualize_with_sidebar(G, output_html):
    net = Network(notebook=False, cdn_resources="remote", directed=True)
    for node, data in G.nodes(data=True):
        if data.get("authors") and len(data["authors"])>1:
            first_author = data["authors"][0]+" et, al."
        elif data.get("authors") and len(data["authors"])>0:
            first_author = data["authors"][0]
        else:
            first_author = "Unknown"
        label = f"{first_author}, {data.get('year', '')}"
        color = "yellow" if data.get("highlight") else "lightblue"
        title = f"{data.get('title', '')} <br> Role: {data.get('role', '')}"
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
    <div style="display:flex; height:100vh;">
      <div style="flex:2; border-right:1px solid #ccc;">
        {{ graph_html|safe }}
      </div>
      <div style="flex:1; padding:1em; overflow-y:scroll;">
        <h2>References</h2>
        <ul>
        {% for doi, data in nodes %}
          <li>
            <b>{{ data.title }}</b> ({{ data.year }})<br>
            {{ data.authors|join(", ") }}<br>
            <a href="https://doi.org/{{ doi }}">{{ doi }}</a>
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

# -------------------------------
# 6. 保存 (JSON)
# -------------------------------
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
# -------------------------------
# YAMLに保存
# -------------------------------
def save_as_yaml(G, output_yaml):
    data = {
        "nodes": [
            {"id": n, **d} for n, d in G.nodes(data=True)
        ],
        "edges": [
            {"source": u, "target": v} for u, v in G.edges()
        ]
    }
    with open(output_yaml, "w") as f:
        yaml.dump(data, f, allow_unicode=True)
# -------------------------------
# 既存graphデータを読み込み
# -------------------------------
def load_from_json(input_json):
    with open(input_json, "r") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for node in data["nodes"]:
        node_id = node.pop("id")
        G.add_node(node_id, **node)
    for edge in data["edges"]:
        G.add_edge(edge["source"], edge["target"])
    return G

def export_bibtex(input_json):
    return None
# -------------------------------
# メイン
# -------------------------------
def main():
    parser = argparse.ArgumentParser(description="文献ネットワーク可視化ツール")
    parser.add_argument("--doi", required=True, help="起点となるDOI")
    parser.add_argument("--concurrency", default = 5, help = "最大非同期処理プロセス数. default:5")
    parser.add_argument("--max_deg", default = 5, help = "文献当たりの cite/ref 最大探索文献数. default:5")
    parser.add_argument("--depth", type=int, default=1, help="探索の深さ default:1")
    parser.add_argument("--html", default="graph.html", help="出力HTMLファイル名")
    parser.add_argument("--json", default="graph.json", help="出力JSONファイル名")
    parser.add_argument("--yaml", default="graph.yaml", help="出力YAMLファイル名")
    parser.add_argument("--resume", default="graph.json", help="入力JSONファイル名")
    args = parser.parse_args()

    print(f"[INFO] DOI={args.doi}, depth={args.depth}")
    G = build_graph(args.doi, depth=args.depth)
    print(f"[INFO] Saving graph to {args.html}, {args.json} and {args.yaml}")
    visualize_graph(G, args.html)
    save_as_json(G, args.json)
    save_as_yaml(G, args.yaml)
    print("[INFO] Done.")


if __name__ == "__main__":
    # 実行時間計測のための付加処理
    import cProfile
    import pstats
    prof = cProfile.Profile()
    prof.enable()
    
    main()
    
    prof.disable()
    stat = pstats.Stats(prof).sort_stats("cumulative")
    stat.print_stats(30)