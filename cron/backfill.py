"""Backfill embeddings and clusters for kudos data.

Requires: DATABASE_URL, EMBEDDING_URI, CHAT_URI env vars (or .env file).
"""
import os
import numpy as np
import requests
import psycopg
from pgvector.psycopg import register_vector
from dotenv import load_dotenv
from collections import Counter
from sklearn.cluster import KMeans

load_dotenv()

_EMBED_BATCH = 256

def compute_embeddings(texts):
    all_vecs = []
    for i in range(0, len(texts), _EMBED_BATCH):
        resp = requests.post(f"{os.environ['EMBEDDING_URI']}/v1/embeddings",
            json={"input": texts[i:i + _EMBED_BATCH]}, timeout=60)
        resp.raise_for_status()
        all_vecs.extend(d["embedding"][:128] for d in resp.json()["data"])
    vecs = np.array(all_vecs)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return vecs / norms

def summarize_cluster(texts):
    msgs = "\n".join(f"- {t}" for t in texts)
    resp = requests.post(f"{os.environ['CHAT_URI']}/v1/chat/completions", timeout=30, json={
        "messages": [{"role": "user", "content":
            f"Here are messages that share a common theme:\n{msgs}\n\n"
            "What is the common theme? Reply with only a short topic label."}],
        "max_tokens": 50})
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()

def main():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        backfill_embeddings(conn)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        register_vector(conn)
        backfill_clusters(conn)
    print("Backfill complete.")

def backfill_embeddings(conn):
    rows = conn.execute(
        "SELECT id, message_text FROM kudos "
        "WHERE embedding IS NULL AND message_text IS NOT NULL").fetchall()
    if not rows:
        print("No kudos need embedding backfill.")
        return
    print(f"Backfilling embeddings for {len(rows)} kudos...")
    ids, texts = zip(*rows)
    emb = compute_embeddings(list(texts))
    with conn.cursor() as cur:
        for kid, vec in zip(ids, emb):
            cur.execute("UPDATE kudos SET embedding = %s WHERE id = %s", (vec, kid))
    print(f"  Done. {len(rows)} embeddings written.")

def _fit_clusters(embeddings, k, prev_centers, sample_weight):
    if prev_centers is not None and len(prev_centers) <= k:
        init = prev_centers if len(prev_centers) == k else \
            np.vstack([prev_centers, KMeans(n_clusters=k - len(prev_centers), n_init=1).fit(embeddings).cluster_centers_])
        return KMeans(n_clusters=k, init=init, n_init=1).fit(embeddings, sample_weight=sample_weight)
    return KMeans(n_clusters=k, n_init=10).fit(embeddings, sample_weight=sample_weight)

def backfill_clusters(conn):
    rows = conn.execute(
        "SELECT id, embedding, message_text, to_char(created_at, 'YYYY-MM') AS month "
        "FROM kudos "
        "WHERE embedding IS NOT NULL").fetchall()
    if len(rows) < 2:
        print(f"Not enough kudos to cluster ({len(rows)}).")
        return
    ids = [r[0] for r in rows]
    texts = [r[2] for r in rows]
    months = [r[3] for r in rows]
    embeddings = np.array([r[1] for r in rows])
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings /= np.where(norms == 0, 1, norms)
    month_counts = Counter(months)
    sample_weight = np.array([1.0 / np.log(1 + month_counts[m]) for m in months])
    prev_centers = conn.execute("SELECT center FROM clusters ORDER BY id").fetchall()
    prev_centers = np.array([r[0] for r in prev_centers]) if prev_centers else None
    k = min(int(len(month_counts) + 0.75 + 3), len(rows) - 1)
    print(f"Clustering {len(rows)} kudos (k={k})...")
    model = _fit_clusters(embeddings, k, prev_centers, sample_weight)
    labels = model.labels_
    centers = model.cluster_centers_

    # Write clusters to DB
    conn.execute("DELETE FROM cluster_members")
    conn.execute("DELETE FROM clusters")
    rng = np.random.default_rng(0)
    for label in sorted(set(labels)):
        mask = labels == label
        cluster_texts = [texts[i] for i, m in enumerate(mask) if m]
        cluster_emb = embeddings[mask]
        # Pick representative texts near center for summarization
        dists = np.linalg.norm(cluster_emb - centers[label], axis=1)
        top_25 = np.argsort(dists)[:max(1, len(cluster_texts) // 4)]
        k_rep = max(5, mask.sum() // 10)
        rep_idx = rng.choice(top_25, size=min(k_rep, len(top_25)), replace=False)
        reps = [cluster_texts[i] for i in rep_idx]
        summary = summarize_cluster(reps)
        cluster_id = conn.execute(
            "INSERT INTO clusters (summary, center) VALUES (%s, %s) RETURNING id",
            (summary, centers[label])).fetchone()[0]
        cluster_kid = [ids[i] for i, m in enumerate(mask) if m]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO cluster_members (cluster_id, kudos_id) VALUES (%s, %s)",
                [(cluster_id, kid) for kid in cluster_kid])

if __name__ == "__main__":
    main()
