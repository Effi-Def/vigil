"""
Matcher unificato Vigil: euristico + semantico.

Questo modulo è il punto d'ingresso unico per "dato un testo, quale
evento del DB è più probabile?". I collector devono importare da qui,
non da vigil.collectors.matcher direttamente.

Strategia:
  1. Matcher euristico (keyword + region boost) — veloce, nessuna dipendenza esterna
  2. Se confidence < 70, prova il matching semantico via embeddings
  3. Vince chi ha confidence maggiore
  4. Se sentence-transformers non è installato: fallback silenzioso all'euristico

Note:
  - Il parametro use_embeddings=False disabilita il layer semantico
    (utile per test e ambienti senza sentence-transformers).
  - Gli embeddings vengono usati solo quando db_or_events è una sessione
    SQLAlchemy attiva (non una lista precaricata).
"""

import logging
from typing import Optional, Union
from collections.abc import Sequence

logger = logging.getLogger(__name__)


def match_event(
    db_or_events,
    text: str,
    title: str = "",
    use_embeddings: bool = True,
) -> tuple[Optional[str], int]:
    """
    Matcher unificato: tenta prima l'euristico, poi (se necessario) il
    matching semantico via sentence embeddings.

    Args:
        db_or_events: sessione SQLAlchemy oppure lista precaricata di Event.
                      Gli embeddings sono attivi solo con una Session.
        text:         testo dell'articolo/post (corpo).
        title:        titolo dell'articolo/post.
        use_embeddings: se False, usa solo il matcher euristico.

    Returns:
        (event_id, confidence) con confidence in [0, 100],
        oppure (None, 0) se nessun match supera la soglia minima.
    """
    from vigil.collectors.matcher import match_event as heuristic_match

    combined_text = f"{title} {text}".strip()

    # ------------------------------------------------------------------ #
    # Step 1 — matcher euristico (keyword + region boost)                 #
    # ------------------------------------------------------------------ #
    h_id, h_conf = heuristic_match(db_or_events, text, title)

    if h_conf >= 70:
        logger.debug(f"Match euristico diretto: {h_id} ({h_conf}%)")
        return h_id, h_conf

    # ------------------------------------------------------------------ #
    # Step 2 — matcher semantico (solo se db è una Session attiva)        #
    # ------------------------------------------------------------------ #
    if use_embeddings:
        from sqlalchemy.orm import Session as _Session
        if isinstance(db_or_events, _Session):
            try:
                from vigil.core.embeddings import semantic_match_event
                s_id, s_conf = semantic_match_event(db_or_events, combined_text)

                if s_conf > h_conf:
                    logger.debug(
                        f"Match semantico vince: {s_id} ({s_conf}% vs euristico {h_conf}%)"
                    )
                    return s_id, s_conf
                elif h_conf >= 30:
                    logger.debug(
                        f"Match euristico vince: {h_id} ({h_conf}% vs semantico {s_conf}%)"
                    )
                    return h_id, h_conf
                else:
                    return None, 0

            except ImportError:
                logger.debug(
                    "sentence-transformers non disponibile, uso solo euristico"
                )

    return (h_id, h_conf) if h_conf >= 30 else (None, 0)
