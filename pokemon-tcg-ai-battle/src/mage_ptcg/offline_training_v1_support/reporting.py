"""Static report generator utility.

Generates self-contained Markdown and HTML reports for evaluation runs.
Ensures HTML escaping and zero external network dependencies.
"""

from __future__ import annotations
import html
import json
from typing import Any

def generate_markdown_report(metrics: dict[str, Any]) -> str:
    """Generate Markdown representation of the evaluation run."""
    lines = [
        "# Evaluation Run Summary Report",
        "",
        f"**Total Games**: {metrics.get('total_games', 0)}",
        f"**Overall Win Rate**: {metrics.get('overall_win_rate', 0.0):.2%}",
        f"**Crashes**: {metrics.get('crash_count', 0)} | **Timeouts**: {metrics.get('timeout_count', 0)}",
        "",
        "## Performance Metrics",
        f"- Legal Action Rate: {metrics.get('legal_action_rate', 1.0):.2%}",
        f"- Fallback Rate: {metrics.get('fallback_rate', 0.0):.2%}",
        ""
    ]
    return "\n".join(lines)

def generate_html_report(metrics: dict[str, Any]) -> str:
    """Generate a self-contained, HTML-escaped summary dashboard."""
    esc_win_rate = html.escape(f"{metrics.get('overall_win_rate', 0.0):.2%}")
    esc_total = html.escape(str(metrics.get("total_games", 0)))

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Evaluation Summary Dashboard</title>
<style>
  body {{ font-family: sans-serif; margin: 40px; background-color: #fafafa; color: #333; }}
  .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); max-width: 600px; }}
  h1 {{ color: #1a73e8; }}
</style>
</head>
<body>
<div class="card">
  <h1>Evaluation Summary</h1>
  <p><strong>Total Games:</strong> {esc_total}</p>
  <p><strong>Overall Win Rate:</strong> {esc_win_rate}</p>
</div>
</body>
</html>
"""
    return html_content
