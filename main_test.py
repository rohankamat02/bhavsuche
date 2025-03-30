from flask import Flask, render_template
import os

app = Flask(__name__, template_folder='Template')

# Debug template folder
print("Current working directory:", os.getcwd())
print("Flask app root path:", app.root_path)
print("Template folder path:", os.path.join(app.root_path, 'Template'))
print("Does Template folder exist?", os.path.exists(os.path.join(app.root_path, 'Template')))
print("Does index.html exist?", os.path.exists(os.path.join(app.root_path, 'Template', 'index.html')))

@app.route('/')
def home():
    return render_template('index.html', message="Hello, this is a test!")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)