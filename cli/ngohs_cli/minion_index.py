import os

from ngo_homesuite.ai.rag_index import LocalRAGIndex


def reindex():
    project_root = os.getenv("NGO_HOMESUITE_PROJECT_ROOT", os.getcwd())
    index_dir = os.getenv("MINION_INDEX_DIR", "data/minion_index")
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    index = LocalRAGIndex(index_dir=index_dir, embed_model=embed_model)
    total = index.build(project_root, user_summary_texts=[])
    print(f"Indexed {total} chunks into {index_dir}")


if __name__ == "__main__":
    reindex()

