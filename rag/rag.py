# -*- coding: utf-8 -*-
"""
纳西妲 RAG 知识库：向量检索 + BM25 关键词 + Rerank
"""
import os, re, json, math, numpy as np
from collections import Counter

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["MODELSCOPE_CACHE"] = os.path.join(_PROJECT_ROOT, "models", "modelscope_cache")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")  # anaconda 的 MKL 与 torch 共存时会报 OMP 冲突

EMBED_DIR = os.path.join(_PROJECT_ROOT, "models", "modelscope_cache", "models", "BAAI--bge-small-zh-v1.5", "snapshots", "master")
RERANK_DIR = os.path.join(_PROJECT_ROOT, "models", "modelscope", "models", "BAAI--bge-reranker-base", "snapshots", "master")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")
VEC_PATH = os.path.join(DATA_DIR, "vectors.npy")
# 向量相似度门槛：低于它的视为与知识库无关，直接跳过（日常聊天不该为剧情检索买单）
VEC_MIN_SCORE = 0.68

_embed_model = None
_embed_tokenizer = None
_rerank_model = None
_rerank_tokenizer = None
_chunks = []
_vectors = None
_bm25 = None


def _get_embed():
    global _embed_model, _embed_tokenizer
    if _embed_model is None:
        from transformers import AutoModel, AutoTokenizer
        import torch
        _embed_tokenizer = AutoTokenizer.from_pretrained(EMBED_DIR)
        _embed_model = AutoModel.from_pretrained(EMBED_DIR)
        _embed_model.eval()
        if torch.cuda.is_available():
            _embed_model = _embed_model.cuda()
    return _embed_model, _embed_tokenizer


def _get_rerank():
    global _rerank_model, _rerank_tokenizer
    if _rerank_model is None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
        _rerank_tokenizer = AutoTokenizer.from_pretrained(RERANK_DIR)
        _rerank_model = AutoModelForSequenceClassification.from_pretrained(RERANK_DIR)
        _rerank_model.eval()
        if torch.cuda.is_available():
            _rerank_model = _rerank_model.cuda()
    return _rerank_model, _rerank_tokenizer


def _encode(texts):
    import torch
    model, tok = _get_embed()
    device = next(model.parameters()).device
    with torch.no_grad():
        enc = tok(texts, padding=True, truncation=True, max_length=512, return_tensors='pt')
        enc = {k: v.to(device) for k, v in enc.items()}
        out = model(**enc)
        mask = enc['attention_mask'].unsqueeze(-1).expand(out.last_hidden_state.size()).float()
        res = torch.sum(out.last_hidden_state * mask, 1) / torch.clamp(mask.sum(1), min=1e-9)
        res = torch.nn.functional.normalize(res, p=2, dim=1)
    return res.cpu().numpy()


def _tokenize(text):
    """简单中文分词：按字 + 英文单词"""
    return list(re.sub(r'[^\u4e00-\u9fff a-zA-Z0-9]', '', text))


def _build_bm25():
    """建 BM25 索引"""
    global _bm25
    corpus = [_tokenize(c) for c in _chunks]
    N = len(corpus)
    df = Counter()
    for doc in corpus:
        for w in set(doc):
            df[w] += 1
    idf = {w: math.log((N - n + 0.5) / (n + 0.5) + 1) for w, n in df.items()}
    doc_len = [len(d) for d in corpus]
    avg_len = sum(doc_len) / max(N, 1)
    tf_list = [Counter(d) for d in corpus]
    _bm25 = {'corpus': corpus, 'idf': idf, 'tf_list': tf_list, 'doc_len': doc_len, 'avg_len': avg_len}


def _bm25_search(query, top_k=20):
    """BM25 关键词检索"""
    if not _bm25:
        return []
    q_tokens = _tokenize(query)
    scores = []
    for i, tf in enumerate(_bm25['tf_list']):
        score = 0.0
        for w in q_tokens:
            if w not in tf:
                continue
            idf = _bm25['idf'].get(w, 0)
            freq = tf[w]
            dl = _bm25['doc_len'][i]
            avg = _bm25['avg_len']
            score += idf * (freq * (1.5 + 1)) / (freq + 1.5 * (1 - 0.75 + 0.75 * dl / avg))
        scores.append((i, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [i for i, s in scores[:top_k] if s > 0]


def _vector_search(query, top_k=20):
    """向量检索：返回高于相关度阈值的候选下标"""
    qv = _encode([query])[0]
    scores = np.dot(_vectors, qv)
    idx = np.argsort(scores)[::-1][:top_k]
    return [int(i) for i in idx if scores[i] >= VEC_MIN_SCORE]


def _rerank(query, candidates, top_k=3):
    """对候选重排"""
    if not candidates:
        return []
    import torch
    model, tok = _get_rerank()
    pairs = [(query, _chunks[i]) for i in candidates]
    with torch.no_grad():
        enc = tok([p[0] for p in pairs], [p[1] for p in pairs],
                  padding=True, truncation=True, max_length=512, return_tensors='pt')
        device = next(model.parameters()).device
        enc = {k: v.to(device) for k, v in enc.items()}
        out = model(**enc)
        scores = out.logits.squeeze(-1).cpu().numpy()
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    results = []
    for idx, s in ranked[:top_k]:
        if s > 0:
            results.append({"text": _chunks[idx], "score": float(s)})
    return results


def clean_wikitext(text):
    text = re.sub(r'\{\{[^|]*\|([^}]*)\}\}', r'\1', text)
    text = re.sub(r'\{\{[^}]*\}\}', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\[\[[^|\]]*\|([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\]]*)\]\]', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def split_chunks(text, max_len=300):
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks = []
    for p in paragraphs:
        if len(p) <= max_len:
            chunks.append(p)
        else:
            sents = re.split(r'(?<=[。！？])', p)
            buf = ''
            for s in sents:
                if len(buf) + len(s) > max_len:
                    if buf: chunks.append(buf)
                    buf = s
                else:
                    buf += s
            if buf: chunks.append(buf)
    return [c for c in chunks if len(c) >= 10 and re.search(r'[\u4e00-\u9fff]', c)]


def build_index():
    global _chunks, _vectors
    _chunks = []
    for fn in os.listdir(DATA_DIR):
        if fn.endswith('.txt') and not fn.startswith('cache_'):
            with open(os.path.join(DATA_DIR, fn), encoding='utf-8') as f:
                raw = f.read()
            cleaned = clean_wikitext(raw)
            _chunks.extend(split_chunks(cleaned))
    print(f"[RAG] 共 {len(_chunks)} 个文本块")
    _vectors = _encode(_chunks)
    np.save(VEC_PATH, _vectors)
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(_chunks, f, ensure_ascii=False, indent=2)
    _build_bm25()
    print(f"[RAG] 索引已保存")


def load_index():
    global _chunks, _vectors
    if os.path.exists(INDEX_PATH) and os.path.exists(VEC_PATH):
        with open(INDEX_PATH, encoding='utf-8') as f:
            _chunks = json.load(f)
        _vectors = np.load(VEC_PATH)
        _build_bm25()
        print(f"[RAG] 索引已加载: {len(_chunks)} 块")
        return True
    return False


def retrieve(query, top_k=3):
    """混合检索：向量 + BM25 -> rerank。
    与知识库无关的日常聊天直接返回空，省掉 reranker 的推理开销（CPU 上约 2 秒）。"""
    if not _chunks:
        if not load_index():
            return []
    qv = _encode([query])[0]
    scores = np.dot(_vectors, qv)
    idx = np.argsort(scores)[::-1][:20]
    vec_hits = [int(i) for i in idx if scores[i] >= VEC_MIN_SCORE]
    bm25_hits = [i for i in _bm25_search(query, top_k=20) if scores[i] >= VEC_MIN_SCORE - 0.1]
    if not vec_hits and not bm25_hits:
        return []  # 向量和关键词都没命中，和剧情知识库不相关
    candidates = list(dict.fromkeys(vec_hits + bm25_hits))
    # 候选很少（<=3）时直接用向量相似度排序，跳过 reranker 模型推理
    if len(candidates) <= 3:
        scored = sorted(((i, float(scores[i])) for i in candidates), key=lambda x: -x[1])
        return [{"text": _chunks[i], "score": s} for i, s in scored[:top_k] if s > 0]
    return _rerank(query, candidates, top_k=top_k)


if __name__ == "__main__":
    if not load_index():
        build_index()
    for q in ["纳西妲和散兵什么关系", "教令院对纳西妲做了什么", "大慈树王是谁"]:
        print(f"\n问: {q}")
        for r in retrieve(q):
            print(f"  [{r['score']:.2f}] {r['text'][:100]}")
