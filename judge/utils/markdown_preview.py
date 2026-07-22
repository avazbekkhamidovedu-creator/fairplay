"""Shared server-side Markdown preview helpers.

Used by the single-fragment preview templates (Martor / classic forms) and by the
batch JSON endpoint consumed by create_v2 RichEditor. Keeping one entrypoint
avoids drift between “preview pane” and “problem page” rendering.
"""
from judge.jinja2.markdown import markdown


def render_preview_html(content, style='problem'):
    """Return the same HTML string the problem page would show for ``content``."""
    return str(markdown(content or '', style))
