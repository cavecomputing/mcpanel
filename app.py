"""mcpanel entry point.

Development: MCPANEL_PUBLIC_HOST=... MCPANEL_PORT_RANGE=... uv run app.py [--debug] [--host 0.0.0.0] [--port 5000]
First admin: uv run flask --app app create-user <username> --admin
Production:  gunicorn --workers 1 --worker-class gthread --threads 16 app:app (see docker/)
"""
import argparse

from mcpanel import create_app

app = create_app()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the mcpanel development server.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--debug', action='store_true', help='auto-reload and the debugger')
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
