"""
app.py — Flask entry point
Registers all service namespaces under /api
"""

from flask import Flask
from flask_restx import Api
from flask_cors import CORS

from services.functions import ns as functions_ns
from services.vm        import ns as vm_ns
from services.storage   import ns as storage_ns

app = Flask(__name__)
CORS(app)

api = Api(
    app,
    version="1.0",
    title="Azure Pricing Calculator API",
    description="Azure Functions + Virtual Machines pricing",
    prefix="/api",
)

api.add_namespace(functions_ns, path="/functions")
api.add_namespace(vm_ns,        path="/vm")
api.add_namespace(storage_ns,   path="/storage")

if __name__ == "__main__":
    app.run(debug=True, port=5000)