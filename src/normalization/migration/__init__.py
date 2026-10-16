"""Migration PREVIEW (dry run): a canonical employee -> a destination provider's create request.

Separate from the source adapters. Pure functions: no network, no database, no provider write.
"""

from .models import MigrationPreview, MigrationRequest
from .ukg_ready import preview_ukg_ready

__all__ = ["MigrationPreview", "MigrationRequest", "preview_ukg_ready"]
