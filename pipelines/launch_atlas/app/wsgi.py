"""Production WSGI entrypoint for Render and other Python hosts."""

from launch_atlas.app.main import create_app

dash_app = create_app()
server = dash_app.server
