import requests
from requests.auth import HTTPDigestAuth

SOAP = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:wsdl="http://www.onvif.org/ver20/media/wsdl">
  <soap:Body>
    <wsdl:GetSnapshotUri>
      <wsdl:ProfileToken>Profile_1</wsdl:ProfileToken>
    </wsdl:GetSnapshotUri>
  </soap:Body>
</soap:Envelope>"""

SOAP2 = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:wsdl="http://www.onvif.org/ver10/media/wsdl">
  <soap:Body>
    <wsdl:GetProfiles/>
  </soap:Body>
</soap:Envelope>"""

headers = {'Content-Type': 'application/soap+xml; charset=utf-8'}

for cam in ['192.168.1.5', '192.168.1.6']:
    print(f'\n=== {cam} ===')
    # GetProfiles
    for port in [80, 81]:
        for endpoint in ['/onvif/media', '/onvif/media2', '/onvif/Media']:
            try:
                r = requests.post(f'http://{cam}:{port}{endpoint}', data=SOAP2,
                                  headers=headers, auth=HTTPDigestAuth('admin', ''), timeout=5)
                if r.status_code == 200:
                    print(f'  GetProfiles OK: :{port}{endpoint}')
                    print(r.text[:500])
            except Exception:
                pass

    # GetSnapshotUri
    for port in [80, 81]:
        for token in ['Profile_1', 'MainStream', 'SubStream', '000', 'profile1']:
            soap = SOAP.replace('Profile_1', token)
            for endpoint in ['/onvif/media', '/onvif/media2']:
                try:
                    r = requests.post(f'http://{cam}:{port}{endpoint}', data=soap,
                                      headers=headers, auth=HTTPDigestAuth('admin', ''), timeout=5)
                    if r.status_code == 200 and 'Uri' in r.text:
                        print(f'  SnapshotUri OK token={token} :{port}{endpoint}')
                        import re
                        m = re.search(r'<.*?[Uu]ri.*?>(.*?)</.*?[Uu]ri', r.text)
                        if m:
                            print(f'  URL: {m.group(1)}')
                except Exception:
                    pass
