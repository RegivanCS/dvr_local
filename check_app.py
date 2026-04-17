import sys
import traceback

try:
    from app import app as application
    print("app.py carregado OK")
    print("Rotas:", [str(r) for r in application.url_map.iter_rules()])
except Exception as e:
    print("ERRO ao carregar app.py:")
    traceback.print_exc()
