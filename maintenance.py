from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PanelProMax | Quick Refresh</title>
        <meta http-equiv="refresh" content="2">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white flex items-center justify-center min-h-screen">
        <div class="text-center p-6 bg-slate-800 rounded-xl shadow-2xl border border-yellow-500/30 max-w-md mx-auto">
            <div class="animate-spin inline-block w-12 h-12 border-4 border-yellow-500 border-t-transparent rounded-full mb-4"></div>
            <h1 class="text-2xl font-bold text-yellow-500 mb-2">Syncing with Server...</h1>
            <p class="text-slate-400 mb-4">We're updating the system to keep your orders lightning fast. Don't close this page!</p>
            <div class="text-xs font-mono text-slate-500 uppercase tracking-widest">
                Reconnecting in progress
            </div>
        </div>
    </body>
    </html>
    """), 503
