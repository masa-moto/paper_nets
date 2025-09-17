# このレポジトリについて
## 何ができるか
以下の機能を考えています。随時編集予定。
- doiに基づく論文情報について、特にその引用被引用関係をグラフにして表示できるようにする処理
- 文献管理を行い、bibtexなどでexportする処理（未実装）

ゆくゆくはウェブアプリのように機能を公開することを考えていますが、まだpythonで処理を実装してhtmlファイルを作成するくらいしかできていません。

## 環境について
python使って実装しています。

argparse , 
requests, 
networkx, 
pyvis.network, 
json, 
tqdm, 

をimportしています。必要に応じてpipなどで環境を用意してください。

## 使い方
### async_doi_graph.pyを実行する

python3 async_doi_graph.py --doi your-doi

を実行することでgraph.htmlを出力します。ブラウザなどでこのhtmlファイルを眺めると、文献の引用/被引用の情報がグラフ構造として確認できます。

一度実行した際にキャッシュとしてJSONファイルをいくつか出力します。これを利用してAPIの呼び出しなどを最小化したり探索を効率化できます。

JSONを読み込んで実行するには

python3 async_doi_graph.py --doi your-doi --resume graph.json --cite cites_cache.json --meta meta_cache.json

等としてください。

### オプション変数など
いくつかのオプション変数を受け付けるようにしています。以下で詳説します


"--doi": 起点となるDOI。これだけは指定必須。

"--concurrency": 最大非同期処理プロセス数. default:5. 多すぎるとAPI呼び出しなどで制限に引っかかるかもしれません。

"--max_deg": 文献当たりの cite/ref 最大探索文献数. default:5。少なすぎるとあまり情報を得られず、多すぎると処理に時間がかかります。

"--depth": 探索の深さ default:1。これとmax_degの数次第で処理時間が指数的に増大するので注意が必要です

"--html": 出力HTMLファイル名。処理終了後にブラウザなどでこのhtmlファイルを確認してください。

"--json": 出力JSONファイル名。キャッシュとしてグラフ構造をJSONに保存します。resumeにも使います。

"--yaml": 出力YAMLファイル名。内容はgraph.jsonとほぼ同様です。

"--resume": 入力JSONファイル名 (グラフ構造)。resume用。これを指定することで、既存グラフに新たなdoiを指定して探索を継続できます。

"--meta": 入力JSONファイル名 (meta情報)。resume用。--resumeを使用する場合はこちらも指定してください。今後統一する可能性はありますが、今のところはとりあえず。

"--cites": 入力JSONファイル名 (cites情報)。resume用。--resumeを使用する場合はこちらも指定してください。

# このレポジトリの作者について
「ネットワーク科学」や、「フラクタル」をキーワードとする大学院生です。