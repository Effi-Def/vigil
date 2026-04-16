"""
Semantic embeddings per il matching evento↔testo.

Usa il modello sentence-transformers paraphrase-multilingual-MiniLM-L12-v2
(supporto multilingua, incluso italiano):
  - Lazy loading: il modello viene scaricato/caricato al primo utilizzo,
    non allo startup, per non rallentare il boot.
  - Cache in-memory degli embedding degli eventi attivi.
  - Cosine similarity mappata a un confidence score 0-100.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy model
# ---------------------------------------------------------------------------

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(
            "Caricamento modello embeddings 'paraphrase-multilingual-MiniLM-L12-v2' "
            "(prima volta — potrebbe richiedere un download)..."
        )
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        logger.info("Modello embeddings pronto")
    return _model


# ---------------------------------------------------------------------------
# Cache embeddings eventi
# ---------------------------------------------------------------------------

_event_embeddings: dict[str, object] = {}  # str → np.ndarray


def get_event_embedding(event_id: str, event_text: str):
    """Ritorna l'embedding cachato o lo calcola al volo."""
    if event_id not in _event_embeddings:
        _event_embeddings[event_id] = get_model().encode(event_text)
    return _event_embeddings[event_id]


def invalidate_event_cache(event_id: str) -> None:
    """Da chiamare quando un evento viene aggiornato o eliminato."""
    _event_embeddings.pop(event_id, None)


def build_event_text(event) -> str:
    """
    Testo rappresentativo dell'evento per l'embedding.
    Ripete i campi più discriminanti (title, region) per dare loro peso maggiore.
    """
    parts = [
        event.title or "",
        event.title or "",       # peso doppio
        event.type or "",
        event.region or "",
        event.region or "",      # peso doppio
        event.status or "",
    ]
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Matching semantico principale
# ---------------------------------------------------------------------------

def semantic_match_event(
    db,
    text: str,
    threshold: float = 0.35,
    top_k: int = 1,
) -> tuple[Optional[str], int]:
    """
    Trova l'evento più semanticamente simile al testo dato.

    Args:
        db: sessione SQLAlchemy
        text: testo da matchare (es. titolo + corpo articolo)
        threshold: similarità coseno minima (0-1), default 0.35
        top_k: numero di candidati da considerare (attualmente usa solo il migliore)

    Returns:
        (event_id, confidence) oppure (None, 0)

    Mappatura similarity → confidence:
        sim >= 0.80 → 95
        sim >= 0.60 → 80
        sim >= 0.45 → 65
        sim >= 0.35 → 50
        <  threshold → (None, 0)
    """
    import numpy as np
    from vigil.core.models import Event

    if not text or len(text.strip()) < 10:
        return None, 0

    events = db.query(Event).all()
    if not events:
        return None, 0

    model = get_model()
    # Tronca a 512 caratteri per mantenere la velocità
    text_embedding = model.encode(text[:512])

    best_id: Optional[str] = None
    best_sim: float = 0.0

    for event in events:
        event_text = build_event_text(event)
        if not event_text.strip():
            continue
        event_emb = get_event_embedding(event.id, event_text)

        norm_product = (
            float(np.linalg.norm(text_embedding)) * float(np.linalg.norm(event_emb))
        ) + 1e-8
        sim = float(np.dot(text_embedding, event_emb)) / norm_product

        if sim > best_sim:
            best_sim = sim
            best_id = event.id

    if best_sim < threshold:
        return None, 0

    if best_sim >= 0.80:
        confidence = 95
    elif best_sim >= 0.60:
        confidence = 80
    elif best_sim >= 0.45:
        confidence = 65
    else:
        confidence = 50

    return best_id, confidence


# ---------------------------------------------------------------------------
# Pulizia cache periodica
# ---------------------------------------------------------------------------

def cleanup_embedding_cache(db) -> None:
    """
    Rimuove dalla cache gli embedding di eventi non più presenti nel DB.
    Da chiamare periodicamente (es. ogni ora) tramite lo scheduler.
    """
    from vigil.core.models import Event

    active_ids = {str(row[0]) for row in db.query(Event.id).all()}
    stale = [k for k in list(_event_embeddings) if k not in active_ids]
    for k in stale:
        del _event_embeddings[k]
    if stale:
        logger.info(
            f"Cache embeddings: rimossi {len(stale)} eventi scaduti "
            f"({len(_event_embeddings)} rimasti)"
        )
