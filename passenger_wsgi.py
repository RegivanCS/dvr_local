import os
# Chave fixa garante que TODOS os workers do Passenger usem a mesma chave.
_KEY = '603471312a89d6564de90d4f111e6393e94045d7917550d1a792da4f922113c6'
os.environ['DVR_SECRET_KEY'] = _KEY  # sobrescreve qualquer valor anterior
from app import app as application
application.secret_key = _KEY  # garante diretamente no objeto, sem depender de os.environ
