import requests
import json

# Credenciais
DVR_USER = 'admin'
DVR_PASSWORD = '!Rede!123'
DVR_URL_REMOTE = 'https://dvr.regivan.tec.br'

# Headers
HTTP_HEADERS = {
    'User-Agent': 'DVR-Tunnel/1.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': DVR_URL_REMOTE,
    'Referer': DVR_URL_REMOTE + '/login',
}

# Carregar configuração local
with open('cameras_config.json', 'r') as f:
    config = json.load(f)

cameras = config['cameras']

print('Fazendo login no DVR remoto...')
s = requests.Session()
s.headers.update(HTTP_HEADERS)

r = s.post(f'{DVR_URL_REMOTE}/login', data={
    'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
}, allow_redirects=True, timeout=10)

print('Login status:', r.status_code)

# Obter lista atual de câmeras
r = s.get(f'{DVR_URL_REMOTE}/api/cameras', timeout=10)
current_cameras = r.json()['cameras']

print('Câmeras atuais no DVR remoto:')
for cam in current_cameras:
    cam_id = cam['id']
    name = cam['name']
    ip = cam['ip']
    print('  ' + cam_id + ': ' + name + ' -> ' + ip)

# Tentar editar as câmeras existentes com as URLs corretas
print('\nTentando editar câmeras existentes...')
local_cams = list(cameras.values())

for i, cam in enumerate(current_cameras):
    if i < len(local_cams):
        local_cam = local_cams[i]
        cam_id = cam['id']
        print('Editando câmera ' + cam_id + ' com dados de ' + local_cam['name'] + '...')
        
        data = {
            'name': local_cam['name'],
            'ip': local_cam['ip'],
            'port': local_cam['port'],
            'user': local_cam['user'],
            'password': local_cam['password'],
            'model': local_cam['model'],
            'path': local_cam['path'],
            'skip_test': 'true',
        }
        
        r = s.post(DVR_URL_REMOTE + '/api/camera/edit/' + cam_id, data=data, timeout=15)
        print('  Status:', r.status_code)
        if r.status_code == 200:
            result = r.json()
            if result.get('success'):
                print('  ✓ Editada com sucesso')
            else:
                print('  ✗ Erro:', result.get('error', 'Unknown'))
        else:
            print('  ✗ Falha na requisição')