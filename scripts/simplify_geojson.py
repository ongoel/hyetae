# GeoJSON 경량화: 좌표 정밀도 축소 + 폴리곤 단순화(위상 보존) + 미니파이.
# Cloudflare Pages(25 MiB)·jsDelivr(20 MB) 한도 통과 + 전국 확장 대비 목적.
# shapely 의존(이미 process_data.py에서 사용 중). 재실행 가능.
import json
import os
import sys
from shapely.geometry import shape, mapping

SRC = os.path.join('data', 'used', '전북특별자치도 bnd_dong_35_2025_2Q.json')
DST = SRC  # 같은 경로 덮어쓰기 (index.html / process_data.py 경로 불변)
PRECISION = 5          # 소수점 자리수 (5 ≈ 1.1m, 등치지도 충분)
TOLERANCE = 0.00012    # 단순화 허용오차(도). ≈ 13m. 도(道) 단위 지도에서 시각 차이 없음


def round_coords(obj, nd):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, int):
        return obj
    if isinstance(obj, (list, tuple)):
        return [round_coords(x, nd) for x in obj]
    return obj


def main():
    with open(SRC, encoding='utf-8') as f:
        gj = json.load(f)

    feats = gj['features']
    for ft in feats:
        geom = shape(ft['geometry'])
        simp = geom.simplify(TOLERANCE, preserve_topology=True)
        if simp.is_empty or not simp.is_valid:
            simp = geom  # 단순화 실패 시 원본 유지
        m = mapping(simp)
        m['coordinates'] = round_coords(m['coordinates'], PRECISION)
        ft['geometry'] = m

    with open(DST, 'w', encoding='utf-8') as f:
        json.dump(gj, f, ensure_ascii=False, separators=(',', ':'))

    mib = os.path.getsize(DST) / 1048576
    print(f'features={len(feats)} -> {DST}')
    print(f'size={mib:.2f} MiB (precision={PRECISION}, tolerance={TOLERANCE})')
    if mib >= 25:
        print('WARNING: still >= 25 MiB — increase TOLERANCE', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
