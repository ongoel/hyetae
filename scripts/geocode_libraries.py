"""전북 공공도서관 주소 → 좌표 지오코딩 (카카오 로컬 API)

사용:
  $env:KAKAO_REST_API_KEY="..."; python scripts/geocode_libraries.py

출력: data/used/공공도서관_좌표.json
형식: [{name, addr, lat, lon, kakao_road_addr, source}, ...]
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_CSV  = os.path.join(ROOT, 'data', 'used', "2026년('25년 실적) 통계조사 결과 - 원자료_분석용.csv")
OUT_PATH = os.path.join(ROOT, 'data', 'used', '공공도서관_좌표.json')

KAKAO_KEY = os.environ.get('KAKAO_REST_API_KEY', '').strip()
if not KAKAO_KEY:
    print('❌ 환경변수 KAKAO_REST_API_KEY 가 설정되어 있지 않습니다.')
    print('   PowerShell:  $env:KAKAO_REST_API_KEY = "<REST API 키>"')
    sys.exit(1)

ADDR_URL    = 'https://dapi.kakao.com/v2/local/search/address.json?query='
KEYWORD_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json?query='
HEADERS = {'Authorization': f'KakaoAK {KAKAO_KEY}'}


def request(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def geocode(name, addr):
    # 1차: 주소 정확 매칭
    if addr:
        try:
            data = request(ADDR_URL + urllib.parse.quote(addr))
            docs = data.get('documents', [])
            if docs:
                d = docs[0]
                return {
                    'lat': float(d['y']),
                    'lon': float(d['x']),
                    'kakao_road_addr': (d.get('road_address') or {}).get('address_name')
                                       or d.get('address_name'),
                    'source': 'address',
                }
        except Exception as e:
            print(f'  ⚠️ 주소API 실패 ({name}): {e}')
    # 2차: 키워드(도서관명) 매칭
    try:
        data = request(KEYWORD_URL + urllib.parse.quote(name + ' 전북'))
        docs = data.get('documents', [])
        if docs:
            d = docs[0]
            return {
                'lat': float(d['y']),
                'lon': float(d['x']),
                'kakao_road_addr': d.get('road_address_name') or d.get('address_name'),
                'source': 'keyword',
            }
    except Exception as e:
        print(f'  ⚠️ 키워드API 실패 ({name}): {e}')
    return None


def main():
    targets = []
    with open(LIB_CSV, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if '전북' not in row.get('지역', ''):
                continue
            targets.append((row.get('도서관명', '').strip(), row.get('주소', '').strip()))

    print(f'🏛️ 지오코딩 대상: {len(targets)}개 도서관')
    results = []
    failed = []
    for i, (name, addr) in enumerate(targets, 1):
        if not name:
            continue
        info = geocode(name, addr)
        if info:
            results.append({'name': name, 'addr': addr, **info})
            print(f'  [{i:>2}/{len(targets)}] ✓ {name} ({info["source"]})')
        else:
            failed.append(name)
            print(f'  [{i:>2}/{len(targets)}] ✗ {name}  주소={addr}')
        time.sleep(0.06)  # 카카오 API rate 안전 여유

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 저장: {OUT_PATH}')
    print(f'   성공 {len(results)}건 / 실패 {len(failed)}건')
    if failed:
        print('   실패 목록 (수동 보완 필요):')
        for n in failed:
            print(f'     - {n}')


if __name__ == '__main__':
    main()
