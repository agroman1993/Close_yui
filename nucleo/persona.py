# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Persona configurable: nombre de la asistente y vocativo del dueno.

Los prompts del proyecto llevan las marcas __NOMBRE__ y __VOCATIVO__. Al
construir cada pieza se sustituyen por lo que diga config.json (bloque
"persona"), de modo que el mismo codigo sirve para cualquier personaje sin
tocar los prompts. Si no hay bloque "persona", se usan los valores por
defecto, que reproducen el comportamiento original ya probado.
"""

NOMBRE_DEFECTO = "Yui"
VOCATIVO_DEFECTO = "Papá"


def persona_de(cfg):
    """Devuelve (nombre, vocativo) leidos de la config, con defectos."""
    p = cfg.get("persona") or {}
    nombre = (p.get("nombre") or NOMBRE_DEFECTO).strip() or NOMBRE_DEFECTO
    vocativo = (p.get("vocativo") or VOCATIVO_DEFECTO).strip() or VOCATIVO_DEFECTO
    return nombre, vocativo


def aplicar(texto, cfg):
    """Sustituye __NOMBRE__ y __VOCATIVO__ en un prompt por la persona real."""
    nombre, vocativo = persona_de(cfg)
    return texto.replace("__NOMBRE__", nombre).replace("__VOCATIVO__", vocativo)
