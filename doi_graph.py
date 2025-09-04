import argparse
import requests
import networkx as nx
from pyvis.network import Network
import json
from tqdm import tqdm
import yaml
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
def fetch_references(doi):
    print('fr')
    url = f"https://api.crossref.org/works/{doi}"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    item = r.json()["message"]
    _list = []
    for ref in tqdm(item.get("reference", [])):
        if "DOI" in ref:
            _list.append(ref.get("DOI"))
    # _list =  [ref.get("DOI") for ref in item.get("reference", []) if "DOI" in ref]
    return _list

# -------------------------------
# 3. 被引用文献（citations, OpenCitations API）
# -------------------------------
def fetch_citations(doi, max_citations=50):
    url = f"https://opencitations.net/index/coci/api/v1/citations/{doi}"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    _list = []
    print(f"Total citations avaiable: {len(data)}. Limiting to {max_citations}")
    for d in tqdm(data[:max_citations], desc="fetch citations"):
        _list.append(d["citing"])
    return _list  
    # return [d["citing"] for d in data]

# -------------------------------
# 4. グラフ構築
# -------------------------------
def build_graph(start_doi, depth=1):
    G = nx.DiGraph()
    visited = set()

    def add_node_with_metadata(doi, highlight=False):
        if doi not in G:
            meta = fetch_metadata(doi) or {"doi": doi, "title": "Unknown"}
            meta["highlight"] = highlight
            G.add_node(doi, **meta)

    def explore(doi, current_depth):
        if current_depth >= depth or doi in visited:
            return
        visited.add(doi)
        # 引用文献（出ていくエッジ）
        if current_depth < depth:
            for ref_doi in fetch_references(doi):
                add_node_with_metadata(ref_doi)
                G.add_edge(doi, ref_doi)
                explore(ref_doi, current_depth + 1)
            # 被引用文献（入ってくるエッジ）
            for cit_doi in fetch_citations(doi):
                add_node_with_metadata(cit_doi)
                G.add_edge(cit_doi, doi)
                explore(cit_doi, current_depth + 1)

    add_node_with_metadata(start_doi, highlight=True)
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
        label = f"{data.get('authors', [''])[0]} et al., {data.get('year', '')}"
        color = "yellow" if data.get("highlight") else "lightblue"
        net.add_node(node, label=label, title=data.get("title", ""), color=color)
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

# -------------------------------
# メイン
# -------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="文献ネットワーク可視化ツール")
    parser.add_argument("--doi", required=True, help="起点となるDOI")
    parser.add_argument("--depth", type=int, default=1, help="探索の深さ (default=1)")
    parser.add_argument("--html", default="graph.html", help="出力HTMLファイル名")
    parser.add_argument("--json", default="graph.json", help="出力JSONファイル名")
    parser.add_argument("--yaml", default="graph.yaml", help="出力YAMLファイル名")
    parser.add_argument("--resume", default="graph.json", help="入力JSONファイル名")
    
    args = parser.parse_args()

    print(f"[INFO] DOI={args.doi}, depth={args.depth}")
    G = build_graph(args.doi, depth=args.depth)
    print("graph builded.")
    print(f"[INFO] Saving graph to {args.html}, {args.json} and {args.yaml}")
    visualize_graph(G, args.html)
    save_as_json(G, args.json)
    save_as_yaml(G, args.yaml)
    print("[INFO] Done.")
