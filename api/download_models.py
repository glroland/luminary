"""Download every model the API loads at runtime into the local caches.

Run by api/Containerfile (image build) and `make install-models` (local
dev), so both populate the cache from the exact same list. Mirrors
chat.py's _load_florence(), embeddings.py's _load_embedder(),
recognition.py's _load_embedder() (model id, trust_remote_code,
pretrained="vggface2") and pet_detection.py's _load_detector(). Where the files land is decided by the caller via
HF_HOME / TORCH_HOME, not here. Needs network access.
"""

import os


def main() -> None:
    import torch
    from facenet_pytorch import InceptionResnetV1
    from sentence_transformers import CrossEncoder, SentenceTransformer
    from transformers import AutoModelForCausalLM, AutoProcessor

    florence = os.environ.get("LUMINARY_FLORENCE_MODEL", "microsoft/Florence-2-base")
    embedding = os.environ.get("LUMINARY_EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    reranker = os.environ.get("LUMINARY_RERANKER_MODEL", "BAAI/bge-reranker-base")

    AutoModelForCausalLM.from_pretrained(florence, torch_dtype=torch.float32, trust_remote_code=True)
    AutoProcessor.from_pretrained(florence, trust_remote_code=True)
    InceptionResnetV1(pretrained="vggface2").eval()
    # pet_detection.py's COCO detector (cats/dogs) -- same weights enum it loads.
    from torchvision.models.detection import (
        FasterRCNN_MobileNet_V3_Large_FPN_Weights,
        fasterrcnn_mobilenet_v3_large_fpn,
    )

    fasterrcnn_mobilenet_v3_large_fpn(weights=FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT).eval()
    SentenceTransformer(embedding, device="cpu")
    if reranker:
        CrossEncoder(reranker, device="cpu")

    print(
        f"Cached Florence-2 ({florence}), facenet-pytorch vggface2 weights and "
        f"the embedding model ({embedding}), the reranker ({reranker or 'disabled'}) under HF_HOME={os.environ.get('HF_HOME', '(default)')}, "
        f"TORCH_HOME={os.environ.get('TORCH_HOME', '(default)')}."
    )


if __name__ == "__main__":
    main()
