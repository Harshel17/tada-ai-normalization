"""Provider dispatch: one module per provider, each exposing `normalize(entity, payload, ...)`."""

from ..canonical.enums import EntityType
from . import isolved, prismhr, ukg_ready

ADAPTERS = {"prismhr": prismhr, "isolved": isolved, "ukg-ready": ukg_ready}
ENTITIES = tuple(e.value for e in EntityType)
