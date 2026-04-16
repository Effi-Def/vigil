"""
Auto-discovery dei collector Vigil.

Qualsiasi file .py in vigil/collectors/ che esponga una funzione
  fetch_X(db: Session) -> int
viene caricato automaticamente. Non è necessario toccare questo file
né scheduler.py per aggiungere una nuova fonte.
"""

import importlib
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CollectorPlugin:
    name: str               # nome leggibile (da COLLECTOR_NAME o stem del file)
    module_name: str        # es. "vigil.collectors.gdacs"
    fetch_fn: Callable      # riferimento alla funzione fetch_*
    interval_minutes: int   # frequenza di polling
    enabled: bool           # se False lo scheduler salta il collector


_cached_plugins: Optional[list[CollectorPlugin]] = None


def _is_valid_fetch_fn(obj) -> bool:
    """
    Ritorna True se obj è una funzione con nome che inizia per 'fetch_'
    e può essere chiamata con esattamente un argomento (db: Session):
      - il primo parametro è tipizzato Session o senza type hint
      - tutti i parametri aggiuntivi hanno un valore di default
    """
    if not callable(obj):
        return False
    fn_name = getattr(obj, "__name__", "")
    if not fn_name.startswith("fetch_"):
        return False
    try:
        sig = inspect.signature(obj)
        params = list(sig.parameters.values())
        if not params:
            return False
        # Il primo parametro deve essere Session o senza annotazione
        first = params[0]
        ann = first.annotation
        if ann is not inspect.Parameter.empty:
            ann_name = getattr(ann, "__name__", repr(ann))
            if ann_name != "Session":
                return False
        # Tutti gli altri parametri devono avere un default
        for p in params[1:]:
            if p.default is inspect.Parameter.empty and p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                return False
        return True
    except (ValueError, TypeError):
        return False


def discover_collectors() -> list[CollectorPlugin]:
    """
    Scansiona vigil/collectors/ e restituisce la lista dei CollectorPlugin
    trovati. Il risultato è cachato: chiamate successive ritornano lo stesso
    oggetto senza re-scansionare il filesystem.
    """
    global _cached_plugins
    if _cached_plugins is not None:
        return _cached_plugins

    collectors_dir = Path(__file__).parent.parent / "collectors"
    plugins: list[CollectorPlugin] = []

    for path in sorted(collectors_dir.glob("*.py")):
        stem = path.stem
        # Salta __init__.py e file privati (_*)
        if stem.startswith("_") or stem == "__init__":
            continue

        module_name = f"vigil.collectors.{stem}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            logger.warning(
                f"Collector '{stem}': impossibile importare il modulo ({exc}), skip"
            )
            continue

        # Cerca la prima funzione fetch_* valida nel modulo
        fetch_fn: Optional[Callable] = None
        for attr_name in sorted(dir(module)):
            obj = getattr(module, attr_name, None)
            # Assicurati che la funzione sia definita in questo modulo
            # (evita di raccogliere funzioni importate da altri moduli)
            if obj is None:
                continue
            fn_module = getattr(obj, "__module__", None)
            if fn_module != module_name:
                continue
            if _is_valid_fetch_fn(obj):
                fetch_fn = obj
                break

        if fetch_fn is None:
            logger.warning(
                f"Collector '{stem}': nessuna funzione fetch_*(db: Session) trovata, skip"
            )
            continue

        name: str = getattr(module, "COLLECTOR_NAME", stem)
        interval: int = int(getattr(module, "COLLECTOR_INTERVAL", 15))
        enabled: bool = bool(getattr(module, "COLLECTOR_ENABLED", True))

        plugin = CollectorPlugin(
            name=name,
            module_name=module_name,
            fetch_fn=fetch_fn,
            interval_minutes=interval,
            enabled=enabled,
        )
        plugins.append(plugin)
        logger.info(
            f"Collector caricato: '{name}' ({module_name}) "
            f"ogni {interval}min — enabled={enabled}"
        )

    _cached_plugins = plugins
    logger.info(f"Registry: {len(plugins)} collector caricati")
    return _cached_plugins
