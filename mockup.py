import requests
import networkx as nx
from pyvis.network import Network

def fetch_references(doi):
    """Crossrefから引用先文献を取得"""
    url = f"https://api.crossref.org/works/{doi}"
    r = requests.get(url).json()
    refs = []
    if "reference" in r["message"]:
        for ref in r["message"]["reference"]:
            if "DOI" in ref:
                refs.append(ref["DOI"].lower())
    return refs

def fetch_citations(doi):
    """OpenCitationsから被引用文献を取得"""
    url = f"https://opencitations.net/index/coci/api/v1/citations/{doi}"
    r = requests.get(url).json()
    return [c["citing"].lower() for c in r]

def build_graph(start_doi, depth=1):
    G = nx.DiGraph()
    frontier = [(start_doi, 0)]  # (doi, 現在の深さ)
    visited = set()

    while frontier:
        doi, d = frontier.pop()
        if doi in visited or d >= depth:
            continue
        visited.add(doi)

        # 引用先
        for ref in fetch_references(doi):
            G.add_edge(doi, ref)   # doi → ref
            frontier.append((ref, d+1))

        # 被引用
        for cit in fetch_citations(doi):
            G.add_edge(cit, doi)   # cit → doi
            frontier.append((cit, d+1))

    return G

# 実行例
doi = "10.1038/nphys1170"
G = build_graph(doi, depth=1)

net = Network(notebook=True,  directed=True)
net.from_nx(G)
net.show("graph.html")
