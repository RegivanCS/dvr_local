import sys
import os

print("Python:", sys.version)
print("Path:", sys.executable)
print("Dir:", os.getcwd())

try:
    import flask
    print("Flask OK:", flask.__version__)
except Exception as e:
    print("Flask ERRO:", e)

try:
    import requests
    print("Requests OK:", requests.__version__)
except Exception as e:
    print("Requests ERRO:", e)
