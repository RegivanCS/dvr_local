"""
Limpeza do DVR remoto — remove todas as câmeras e recadastra apenas as ativas.
Uso: python cleanup_dvr.py
"""
import requests

DVR_URL      = 'https://dvr.regivan.tec.br'
DVR_USER     = 'admin'
DVR_PASSWORD = '!Rede!123'

HTTP_HEADERS = {'User-Agent': 'Mozilla/5.0 (DVR-Camera-Viewer/1.0)'}

# Câmeras a cadastrar após a limpeza
# Deixe vazio para apenas limpar sem recadastrar
# Será preenchido automaticamente com as câmeras do tunnel_relay após rodar
CAMERAS_TO_ADD = []  # preenchido abaixo se tunnel estiver ativo

def login():
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    r = s.post(f'{DVR_URL}/login', data={
        'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
    }, allow_redirects=True, timeout=10)
    if '/login' in r.url:
        print('✗ Falha no login')
        return None
    print('✓ Login OK')
    return s

def get_cameras(session):
    r = session.get(f'{DVR_URL}/api/cameras', timeout=10)
    return r.json() if r.ok else {}

def delete_camera(session, cam_id):
    r = session.post(f'{DVR_URL}/api/camera/delete/{cam_id}', timeout=10)
    return r.json() if r.ok else {}

print('=' * 50)
print('🧹 Limpeza do DVR Remoto')
print('=' * 50)

session = login()
if not session:
    import sys; sys.exit(1)

# Lista todas as câmeras
cameras = get_cameras(session)
print(f'\n📋 Câmeras encontradas: {len(cameras)}')
for cid, cam in cameras.items():
    print(f'  [{cid}] {cam.get("name")} — {cam.get("ip")}:{cam.get("port")}')

if not cameras:
    print('\nNenhuma câmera para remover.')
    import sys; sys.exit(0)

confirm = input(f'\nRemover todas as {len(cameras)} câmeras? (s/N): ').strip().lower()
if confirm != 's':
    print('Operação cancelada.')
    import sys; sys.exit(0)

print('\n🗑️  Removendo...')
for cid, cam in cameras.items():
    result = delete_camera(session, cid)
    if result.get('success'):
        print(f'  ✓ Removida: {cam.get("name")}')
    else:
        print(f'  ✗ Erro: {cam.get("name")} — {result}')

print(f'\n✅ Concluído! DVR limpo.')
print('Execute tunnel_relay.py para recadastrar as câmeras ativas.')
