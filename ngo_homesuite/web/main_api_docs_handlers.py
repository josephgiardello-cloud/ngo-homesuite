from __future__ import annotations

from pathlib import Path

from flask import Response


def api_openapi_spec(spec_path: Path):
    if not spec_path.exists():
        return {"error": "OpenAPI spec not found."}, 404

    return Response(spec_path.read_text(encoding="utf-8"), mimetype="application/yaml")


def api_docs_index(spec_url: str, swagger_url: str):
    html = (
        "<!doctype html>"
        "<html><head><meta charset=\"utf-8\"><title>NGO HomeSuite API Docs</title>"
        "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:2rem;line-height:1.45;}code{background:#f3f3f3;padding:0.15rem 0.35rem;border-radius:4px;}a{color:#0b5cab;}</style>"
        "</head><body>"
        "<h1>NGO HomeSuite API Docs</h1>"
        "<p>Starter API contract for beta integrations.</p>"
        f"<p>OpenAPI spec: <a href=\"{spec_url}\">{spec_url}</a></p>"
        f"<p>Interactive Swagger UI: <a href=\"{swagger_url}\">{swagger_url}</a></p>"
        "<p>Use this spec with Swagger Editor or Redoc for interactive review.</p>"
        "</body></html>"
    )
    return Response(html, mimetype="text/html")


def api_swagger_ui(spec_url: str):
    html = f"""<!doctype html>
<html>
    <head>
        <meta charset=\"utf-8\" />
        <title>NGO HomeSuite Swagger UI</title>
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <link rel=\"stylesheet\" href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\" />
        <style>
            body {{ margin: 0; background: #f6f8fb; }}
            .topbar {{ display: none; }}
        </style>
    </head>
    <body>
        <div id=\"swagger-ui\"></div>
        <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
        <script>
            window.addEventListener('load', function() {{
                SwaggerUIBundle({{
                    url: '{spec_url}',
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    docExpansion: 'list',
                    defaultModelsExpandDepth: 1,
                }});
            }});
        </script>
    </body>
</html>"""
    return Response(html, mimetype="text/html")
