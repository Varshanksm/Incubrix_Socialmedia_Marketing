"""Renderers write the final file and its editable source into content/assets/<asset_id>/."""
from __future__ import annotations


class RenderError(Exception):
    """One asset could not be rendered; it is rejected and the run continues."""
