"""Excel export adapter — wraps export_pipeline.py."""
from __future__ import annotations

from typing import Any


class ExcelAdapter:
    """Unified interface to Excel export."""

    async def export(
        self,
        output_path: str = "data/pipeline_output.xlsx",
    ) -> dict[str, Any]:
        """Export pipeline data to Excel workbook. Returns output path."""
        from outreach_pipeline.export_pipeline import export_pipeline
        path = await export_pipeline(output_path)
        return {"success": True, "path": path}
