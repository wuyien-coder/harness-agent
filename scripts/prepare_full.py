"""Copy local full corpus and reuse verified existing BGE vectors without API calls."""
from pathlib import Path
import sys,shutil,json,time,sqlite3,os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from dotenv import dotenv_values
from mathkb.core.config import EmbeddingSettings
from mathkb.rag.embedding import build_embedding,text_digest
from mathkb.rag.chunking import load_directory

def main():
    started=time.perf_counter()
    old=ROOT.parent/"简历项目_知识问答平台"
    cfg=dotenv_values(old/".env")
    source=Path(cfg["MATHKB_BIOGRAPHIES_DIR"])
    dest=ROOT/"data/full"
    shutil.copytree(old/"data/full",dest,dirs_exist_ok=True)
    shutil.copytree(source,dest/"biographies",dirs_exist_ok=True)
    print("Full corpus copied:",len(list((dest/"biographies").glob("*.txt"))),flush=True)
    embed=build_embedding(EmbeddingSettings(provider="bge",cache_dir=cfg["MATHKB_EMBEDDING__CACHE_DIR"],
      vector_cache_dir=str(ROOT/".index/embedding-cache"),batch_size=64))
    report={"biographies":len(list((dest/"biographies").glob("*.txt"))),"embedding":embed.name,"dimension":embed.dim}
    try:
        # A private copy avoids starting/mutating the original Chroma database.
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        source_index=Path(cfg["MATHKB_VECTOR__PERSIST_DIR"])
        staging=Path(os.environ.get("MATHKB_CHROMA_IMPORT_DIR",str(ROOT/".runtime/chroma-import")))
        if not (staging/"chroma.sqlite3").exists():
            shutil.copytree(source_index,staging,dirs_exist_ok=True)
            original=sqlite3.connect(source_index.joinpath("chroma.sqlite3").as_uri()+"?mode=ro",uri=True)
            with sqlite3.connect(staging/"chroma.sqlite3") as snapshot:
                original.backup(snapshot)
            original.close()
        collection=chromadb.PersistentClient(path=str(staging),settings=ChromaSettings(anonymized_telemetry=False)).get_collection("mathkb",embedding_function=None)
        first=collection.get(limit=3,include=["documents","embeddings"])
        actual=embed.model.encode(first["documents"],normalize_embeddings=True,show_progress_bar=False)
        import numpy as np
        similarities=np.sum(np.asarray(first["embeddings"])*np.asarray(actual),axis=1)
        if float(min(similarities))<.999:
            raise ValueError("Existing index vectors do not match the configured BGE model")
        count=collection.count()
        for offset in range(0,count,1000):
            batch=collection.get(limit=1000,offset=offset,include=["documents","embeddings"])
            embed.cache.put_many(batch["documents"],[list(map(float,v)) for v in batch["embeddings"]])
        report.update(existing_vectors=count,verification_min_cosine=float(min(similarities)))
        print("Verified existing vectors imported:",count,flush=True)
    except Exception as exc:
        report["existing_vector_import_error_type"]=type(exc).__name__
        print("Existing vector reuse unavailable; actual BGE will encode missing chunks:",type(exc).__name__,flush=True)
    chunks=load_directory(dest/"biographies")
    texts=[c.text for c in chunks]
    available=embed.cache.get_many(texts)
    misses=list(dict.fromkeys(t for t in texts if text_digest(t) not in available))
    report.update(chunks=len(chunks),unique_persons=len({c.person_name for c in chunks}),
      cached_chunk_hits=sum(text_digest(t) in available for t in texts),new_unique_texts=len(misses))
    print("Full chunks:",len(chunks),"new texts to encode:",len(misses),flush=True)
    for offset in range(0,len(misses),64):
        embed.encode(misses[offset:offset+64])
        if offset%640==0:
            print("BGE encoding",min(offset+64,len(misses)),"/",len(misses),flush=True)
    report.update(cache_vectors=embed.cache.count(),seconds=round(time.perf_counter()-started,2),status="passed")
    (ROOT/"reports/full_prepare.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Full corpus and actual BGE cache ready.",flush=True)

if __name__=="__main__":
    main()
