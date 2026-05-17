"""
문해력 인프라 취약지수(초등) 산출 스크립트
- 전북특별자치도 읍면동별 취약지수를 계산하여 JSON으로 저장
- 학교기본정보 주소 활용 → 읍면동 단위 집계
- 지도: 전북특별자치도 bnd_dong_35_2025_2Q.json 사용
"""
import csv, json, os, sys, io, re, statistics, bisect
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ============================================================
# 0. 새 GeoJSON (ADM_CD, ADM_NM) 로드 및 읍면동 목록 구성
# ============================================================
print("🗺️ 새 GeoJSON 로드 중...")
GEO_FILE = os.path.join(DATA_DIR, 'used', '전북특별자치도 bnd_dong_35_2025_2Q.json')
with open(GEO_FILE, encoding='utf-8') as f:
    geo_data = json.load(f)

# ADM_CD 5자리 prefix → 시군구 단축명 매핑 (hardcoded, 전북특별자치도 기준)
PREFIX_TO_SGG = {
    '35011': '전주시완산구',
    '35012': '전주시덕진구',
    '35020': '군산시',
    '35030': '익산시',
    '35040': '정읍시',
    '35050': '남원시',
    '35060': '김제시',
    '35510': '완주군',
    '35520': '진안군',
    '35530': '무주군',
    '35540': '장수군',
    '35550': '임실군',
    '35560': '순창군',
    '35570': '고창군',
    '35580': '부안군',
}

# (sgg_short, dong_name) → ADM_CD 조회 테이블
sgg_dong_to_adm = {}
adm_to_props = {}  # ADM_CD → {ADM_CD, ADM_NM, sgg_short}
for feat in geo_data['features']:
    props = feat['properties']
    adm_cd = props['ADM_CD']
    adm_nm = props['ADM_NM']
    prefix = adm_cd[:5]
    sgg = PREFIX_TO_SGG.get(prefix, prefix)
    key = (sgg, adm_nm)
    sgg_dong_to_adm[key] = adm_cd
    adm_to_props[adm_cd] = {'ADM_CD': adm_cd, 'ADM_NM': adm_nm, 'sgg': sgg}

all_adm_cds = set(adm_to_props.keys())
print(f"  읍면동 수: {len(all_adm_cds)}개")

# ============================================================
# 1. 주소에서 시군구 단축명 + 읍면동명 추출
# ============================================================
def normalize_sgg(sgg_raw):
    """시군구명 정규화: 공백 제거, 구 붙이기"""
    sgg_raw = sgg_raw.replace('전북특별자치도', '').replace('전라북도', '').strip()
    parts = sgg_raw.split()
    if len(parts) >= 2 and parts[0].endswith('시') and parts[1].endswith('구'):
        return parts[0] + parts[1]
    if parts:
        return parts[0]
    return sgg_raw

def normalize_dong_name(dong):
    """법정동 표기 → 행정동 표기로 변환.
    예) 인후동1가 → 인후1동, 삼천동2가 → 삼천2동, 나운동 → 나운동(그대로)
    """
    # "X동N가" 패턴: 동 번호를 뒤로 이동
    m = re.match(r'^(.+?)동(\d+)가$', dong)
    if m:
        return m.group(1) + m.group(2) + '동'
    # "동완산동" → "완산동" 처럼 앞에 방향+구 붙은 경우는 그대로 유지
    return dong

def extract_dong_from_addr(addr):
    """주소에서 읍/면/동 토큰 추출. (sgg_short, dong_name) 반환"""
    # 괄호 안 읍면동 먼저 시도: "용성로 49 (동충동)"
    m = re.search(r'\(([^)]+[읍면동가])\)', addr)
    if m:
        dong_raw = m.group(1)
        # 가 붙은 경우만 정규화 (읍/면/동+숫자+가 → 숫자+동)
        dong = normalize_dong_name(dong_raw) if dong_raw.endswith('가') else dong_raw
        before = addr[:m.start()]
    else:
        before = addr
        dong = None

    before = before.replace('전북특별자치도', '').replace('전라북도', '').strip()
    parts = before.split()

    # 시군구 추출
    if len(parts) >= 2 and parts[0].endswith('시') and parts[1].endswith('구'):
        sgg = parts[0] + parts[1]
        rest = parts[2:]
    elif parts:
        sgg = parts[0]
        rest = parts[1:]
    else:
        return None, None

    if dong:
        return sgg, dong

    # rest에서 읍/면/동 찾기 (첫 번째 매칭)
    for token in rest:
        clean = re.sub(r'[0-9\-··\s]', '', token)
        if clean.endswith('읍') or clean.endswith('면') or clean.endswith('동'):
            return sgg, clean
    return sgg, None

def addr_to_adm_cd(addr):
    """주소 문자열 → ADM_CD 반환 (없으면 None)
    1차: 행정동명 직접 매핑 (sgg_dong_to_adm)
    2차: 법정동명 fallback (jibun_to_adm) — 괄호 안 원문 + 정규화 이름 순서로 시도
    """
    sgg, dong = extract_dong_from_addr(addr)
    if not sgg or not dong:
        return None
    # 1차: 행정동명 직접
    result = sgg_dong_to_adm.get((sgg, dong))
    if result:
        return result
    # 2차: 법정동명 fallback — 괄호 안 원문(정규화 전) 시도
    m = re.search(r'\(([^)]+[읍면동가])\)', addr)
    if m:
        raw_dong = m.group(1)
        result = jibun_to_adm.get((sgg, raw_dong))
        if result:
            return result
    # 3차: 정규화된 동명으로 법정동 테이블 시도
    return jibun_to_adm.get((sgg, dong))

# 법정동-행정동 연계 코드 로드: (sgg_short, 법정동명) → ADM_CD
# 코드 체계가 달라(신코드 52xx vs GeoJSON 35xx) 행정동명(읍면동명) 경유로 ADM_CD 조회
print("🗂️ 법정동-행정동 연계 코드 로드 중...")
JIBUN_FILE = os.path.join(DATA_DIR, 'used', '법정동_행정동_연계.csv')
jibun_to_adm = {}
with open(JIBUN_FILE, encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if '전북' not in row.get('시도명', ''):
            continue
        emd = row.get('읍면동명', '').strip()   # 행정동명
        dong_ri = row.get('동리명', '').strip()  # 법정동명
        sgg_raw = row.get('시군구명', '').strip()
        if not emd or not dong_ri:
            continue
        sgg = normalize_sgg(sgg_raw)
        # 행정동명으로 GeoJSON 기반 ADM_CD 조회 (코드가 달라 이름으로 경유)
        adm_cd = sgg_dong_to_adm.get((sgg, emd))
        if adm_cd:
            key = (sgg, dong_ri)
            if key not in jibun_to_adm:
                jibun_to_adm[key] = adm_cd
print(f"  법정동 매핑 항목: {len(jibun_to_adm)}개")

# ============================================================
# 2. 학교기본정보 로드 - 좌표 기반 Point-in-Polygon 매핑
# ============================================================
print("\n🏫 학교기본정보 로드 중 (좌표 → 폴리곤 매핑)...")
from shapely.geometry import Point, shape

# GeoJSON 폴리곤 인덱스 구성
geo_polygons = []  # [(adm_cd, shapely_geom), ...]
for feat in geo_data['features']:
    try:
        geom = shape(feat['geometry'])
        geo_polygons.append((feat['properties']['ADM_CD'], geom))
    except Exception:
        pass

def coord_to_adm_cd(lat, lng):
    """위도/경도로 ADM_CD 반환"""
    pt = Point(float(lng), float(lat))
    for adm_cd, geom in geo_polygons:
        if geom.contains(pt):
            return adm_cd
    # contains 실패 시 최근접 폴리곤 (distance)
    best, best_dist = None, float('inf')
    for adm_cd, geom in geo_polygons:
        d = geom.distance(pt)
        if d < best_dist:
            best_dist, best = d, adm_cd
    return best if best_dist < 0.01 else None

school_info_file = os.path.join(DATA_DIR, 'used', '학교기본정보(초)_전북특별자치도교육청.csv')
school_info = {}  # 학교코드 → {adm_cd, addr, name}

all_school_rows = []
with open(school_info_file, encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    all_school_rows = [r for r in reader if r.get('폐교여부', '') != 'Y']

print(f"  좌표 매핑 중 ({len(all_school_rows)}개 학교)...")
for row in all_school_rows:
    code = row.get('정보공시 학교코드', '').strip()
    addr = row.get('주소내역', '').strip()
    lat_s = row.get('위도', '').strip()
    lng_s = row.get('경도', '').strip()
    adm_cd = None
    if lat_s and lng_s:
        try:
            adm_cd = coord_to_adm_cd(lat_s, lng_s)
        except Exception:
            pass
    if not adm_cd:
        # 주소 파싱 fallback
        adm_cd = addr_to_adm_cd(addr) or addr_to_adm_cd(row.get('학교도로명 주소', ''))
    school_info[code] = {
        'adm_cd': adm_cd,
        'addr': addr,
        'name': row.get('학교명', ''),
        'sgg': normalize_sgg(row.get('지역', ''))
    }

matched = sum(1 for v in school_info.values() if v['adm_cd'])
print(f"  전체 학교: {len(school_info)}개, 읍면동 매칭: {matched}개")

# ============================================================
# 3. 학교도서관 이용현황 로드 (2024년)
# ============================================================
print("\n📚 학교도서관 대출 데이터 로드 중...")
lib_file = os.path.join(DATA_DIR, 'used',
                        '2024년도_학교도서관 현황(이용 현황)(초)_전북특별자치도교육청.csv')

# 지표 = '1인당대출자료수'(= 연간학생대출자료수/전년도전체학생수, CSV 제공) — 표준 도서관 KPI.
# '연간학생대출자수'는 고유 학생 수가 아니라 누적 대출자(거래성) 수라, 학생수로 나누면
# 0~1 참여율이 아니라 수백 %가 나와 지표가 오염됨 → 사용하지 않음.
# 읍면동 내 복수 학교는 중앙값(median)으로 집계 → 극단값 학교 영향 차단.
adm_loans_per_student = defaultdict(list)  # adm_cd → [1인당 대출권수 per school, ...]
adm_students = defaultdict(int)
school_count_by_adm = defaultdict(int)

with open(lib_file, encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row.get('제외여부', '') == 'Y':
            continue
        code = row.get('정보공시 학교코드', '').strip()
        info = school_info.get(code)
        if not info or not info['adm_cd']:
            continue
        adm_cd = info['adm_cd']
        raw = (row.get('1인당대출자료수', '') or '').strip()
        if raw == '':
            continue  # 결측 — 0으로 채우면 invert 후 최대취약으로 오염되므로 제외
        try:
            lps = float(raw)
        except ValueError:
            continue
        try:
            students = int(row.get('전년도전체학생수', '0') or '0')
        except ValueError:
            students = 0
        school_count_by_adm[adm_cd] += 1
        adm_students[adm_cd] += students
        adm_loans_per_student[adm_cd].append(lps)

# 읍면동 대표값: 중앙값
adm_median_loans_per_student = {
    adm: statistics.median(v) for adm, v in adm_loans_per_student.items() if v
}
print(f"  읍면동 학교도서관 1인당대출 데이터 있는 곳: {len(adm_median_loans_per_student)}개")

# --- 학교도서관 콘텐츠: 1인당 장서수 (자료보유 현황 2024) ---
collect_file = os.path.join(DATA_DIR, 'used',
                            '2024년도_학교도서관 현황(자료 보유)(초)_전북특별자치도교육청.csv')
adm_books_per_student = defaultdict(list)
school_students = {}  # 학교코드 → 전체학생수 (운영현황 1인당 예산 산출용)
with open(collect_file, encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        if row.get('제외여부', '') == 'Y':
            continue
        code = row.get('정보공시 학교코드', '').strip()
        info = school_info.get(code)
        if not info or not info['adm_cd']:
            continue
        try:
            school_students[code] = int(row.get('전체학생수', '0') or '0')
        except ValueError:
            pass
        raw = (row.get('1인당장서수', '') or '').strip()
        if raw == '':
            continue
        try:
            adm_books_per_student[info['adm_cd']].append(float(raw))
        except ValueError:
            continue
adm_median_books_per_student = {
    adm: statistics.median(v) for adm, v in adm_books_per_student.items() if v
}
print(f"  읍면동 1인당장서수 데이터 있는 곳: {len(adm_median_books_per_student)}개")

# --- 학교도서관 투자: 1인당 자료구입비예산액 (운영현황 2024) ---
# 사서자격증보유는 전북 초등 232곳 중 229곳이 0이라 변별력 無 → 채택하지 않음.
# 자료구입비예산액/전체학생수: 결측·0원 없음, 5천~175만원 범위로 변별력 우수.
oper_file = os.path.join(DATA_DIR, 'used',
                         '2024년도_학교도서관 현황(운영 현황)(초)_전북특별자치도교육청.csv')
adm_budget_per_student = defaultdict(list)
with open(oper_file, encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        if row.get('제외여부', '') == 'Y':
            continue
        code = row.get('정보공시 학교코드', '').strip()
        info = school_info.get(code)
        if not info or not info['adm_cd']:
            continue
        s = school_students.get(code, 0)
        if s <= 0:
            continue
        raw = (row.get('자료구입비예산액', '') or '').strip()
        if raw == '':
            continue
        try:
            adm_budget_per_student[info['adm_cd']].append(float(raw) / s)
        except ValueError:
            continue
adm_median_budget_per_student = {
    adm: statistics.median(v) for adm, v in adm_budget_per_student.items() if v
}
print(f"  읍면동 1인당 자료구입예산 데이터 있는 곳: {len(adm_median_budget_per_student)}개")

# ============================================================
# 4. 공공도서관 접근성 (읍면동별) - 2025년 실적 통계 활용
# ============================================================
print("\n🏛️ 공공도서관 데이터 처리 중 (2025년 통계 원자료 기반)...")
lib_stat_file = os.path.join(DATA_DIR, 'used', "2026년('25년 실적) 통계조사 결과 - 원자료_분석용.csv")
adm_publib           = defaultdict(int)    # 도서관 수
adm_child_loans      = defaultdict(int)    # 어린이 대출 건수 (참고용)
adm_child_borrowers  = defaultdict(int)    # 어린이 실질 대출자 수
adm_child_target     = defaultdict(int)    # 봉사대상 어린이 수 (분모)
adm_child_collection = defaultdict(int)    # 어린이 인쇄 장서수
adm_reading_program  = defaultdict(int)    # 독서·도서관 관련 프로그램 참가자수
adm_child_members    = defaultdict(int)    # 어린이 회원 등록자 수 → 등록률(=이용 연결도) 산정
adm_mc_service       = defaultdict(int)    # 다문화 서비스 이용수 → 서비스 도달률 산정
adm_mc_service_target= defaultdict(int)    # 봉사대상 다문화 수 (분모)
adm_service_area     = defaultdict(float)  # 서비스 면적(㎡) (참고용)

# 주소 파싱으로 매칭 불가한 도서관 예외 처리 (하드코딩)
# 정읍기적의도서관: 도로명주소에 읍면동 토큰 없음 → 지번주소(정읍시 수성동 1014-1) 기준 수성동 직접 지정
HARDCODED_LIB_ADM = {
    '정읍기적의도서관': sgg_dong_to_adm.get(('정읍시', '수성동')),
}

unmatched_libs = []
with open(lib_stat_file, encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if '전북' not in row.get('지역', ''):
            continue
        lib_nm = row.get('도서관명', '')
        addr = row.get('주소', '')
        # 하드코딩 예외 우선 적용, 없으면 주소 파싱
        adm_cd = HARDCODED_LIB_ADM.get(lib_nm) or addr_to_adm_cd(addr)
        if not adm_cd:
            unmatched_libs.append(lib_nm)
            continue
        adm_publib[adm_cd] += 1
        try:
            adm_child_loans[adm_cd] += int(row.get('인쇄자료대출_어린이_합계', 0) or 0)
        except: pass
        try:
            adm_child_borrowers[adm_cd] += int(row.get('인쇄자료_대출자_어린이', 0) or 0)
        except: pass
        try:
            adm_child_target[adm_cd] += int(row.get('봉사대상자수_어린이', 0) or 0)
        except: pass
        try:
            adm_child_collection[adm_cd] += int(row.get('어린이 자료(인쇄)수', 0) or 0)
        except: pass
        try:
            prog = (int(row.get('오프라인_도서관및독서관련_정기강좌_참가자수', 0) or 0)
                  + int(row.get('오프라인_도서관및독서관련_1회성_참가자수', 0) or 0)
                  + int(row.get('온라인_도서관및독서관련_정기강좌_참가자수', 0) or 0)
                  + int(row.get('온라인_도서관및독서관련_1회성_참가자수', 0) or 0))
            adm_reading_program[adm_cd] += prog
        except: pass
        try:
            adm_child_members[adm_cd] += int(row.get('연령별 회원등록자 수_어린이', 0) or 0)
        except: pass
        try:
            adm_mc_service[adm_cd]        += int(row.get('취약계층서비스이용수_다문화', 0) or 0)
            adm_mc_service_target[adm_cd] += int(row.get('봉사대상자수_다문화', 0) or 0)
        except: pass
        try:
            adm_service_area[adm_cd] += float(row.get('면적_도서관 서비스 제공 면적', 0) or 0)
        except: pass

print(f"  공공도서관 매칭된 읍면동: {len(adm_publib)}개 ({sum(adm_publib.values())}개 도서관)")
print(f"  미매칭 도서관: {len(unmatched_libs)}개 → {unmatched_libs[:5]}")
print(f"  어린이 대출 집계 읍면동: {len(adm_child_loans)}개, 총 대출: {sum(adm_child_loans.values()):,}권")

# ============================================================
# 5. 다문화 현황 (읍면동별, 2023년 합계) + 총 가구수 로드
# ============================================================
print("\n🌍 다문화 현황 데이터 처리 중...")
MC_2023_COL = 69  # 2023 합계 열 인덱스

adm_mc_count = {}

with open(os.path.join(DATA_DIR, 'used', '전북_다문화현황_UTF8.csv'), encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    rows = list(reader)

for row in rows[3:]:
    if len(row) <= MC_2023_COL:
        continue
    sido = row[0].strip().strip('"')
    sgg_raw = row[1].strip().strip('"')
    dong = row[2].strip().strip('"')
    if '전북' not in sido and '전라북' not in sido:
        continue
    val_str = row[MC_2023_COL].strip().strip('"')
    try:
        val = int(val_str)
    except:
        val = 0
    sgg = normalize_sgg(sgg_raw)
    adm_cd = sgg_dong_to_adm.get((sgg, dong))
    if adm_cd:
        adm_mc_count[adm_cd] = val

print(f"  다문화 데이터 매칭된 읍면동: {len(adm_mc_count)}개")

# 총 가구수 로드: 다문화 비율 산정용
# 열: col[0]=읍면동명, col[9]=2024 일반가구_계
# 중복 읍면동명 있음 → 이전 매칭된 시군구로 문맥 판단
print("🏠 총 가구수 데이터 로드 중...")
HH_FILE = os.path.join(DATA_DIR, 'used', '가구원수별_가구__읍면동_20260517001712(UTF-8).csv')
from collections import defaultdict as _dd
_dong_to_adm_list = _dd(list)
for _adm_cd, _props in adm_to_props.items():
    _dong_to_adm_list[_props['ADM_NM']].append(_adm_cd)

adm_households = {}
with open(HH_FILE, encoding='utf-8-sig') as f:
    hh_rows = list(csv.reader(f))

_prev_sgg = None
for row in hh_rows[2:]:
    if len(row) < 10:
        continue
    dong_nm = row[0].strip().strip('"')
    hh_str = row[9].strip().replace(',', '')
    if not dong_nm or hh_str in ('', 'X', '-'):
        continue
    try:
        hh = int(hh_str)
    except:
        continue
    candidates = _dong_to_adm_list.get(dong_nm, [])
    matched = None
    if len(candidates) == 1:
        matched = candidates[0]
    elif len(candidates) > 1:
        # 직전 매칭된 시군구로 중복 읍면동 구분
        if _prev_sgg:
            for _c in candidates:
                if adm_to_props[_c]['sgg'] == _prev_sgg:
                    matched = _c
                    break
        if not matched:
            matched = candidates[0]
    if matched:
        adm_households[matched] = hh
        _prev_sgg = adm_to_props[matched]['sgg']

print(f"  총 가구수 매칭 읍면동: {len(adm_households)}개")

# ============================================================
# 6. 취약지수 산출
# ============================================================
print("\n📊 취약지수 산출 중...")

def min_max_normalize(values_dict, invert=False):
    # 0 포함 전체 값으로 min/max 산정 — 0 제외 시 invert 후 >1.0 버그 발생
    vals = list(values_dict.values())
    if not vals:
        return {k: 0.5 for k in values_dict}
    mn, mx = min(vals), max(vals)
    result = {}
    for k, v in values_dict.items():
        if mx > mn:
            norm = (v - mn) / (mx - mn)
        else:
            norm = 0.5
        if invert:
            norm = 1.0 - norm
        result[k] = round(max(0.0, min(1.0, norm)), 4)
    return result


def percentile_rank_normalize(values_dict, invert=False):
    # 순위 백분위(동순위 평균) 정규화 — 값 간 거리가 아닌 상대순위만 사용하므로
    # 우편향·이상치(최대/중앙값 15배 등)에 면역. 우편향 도서관 지표에 적합.
    items = list(values_dict.items())
    n = len(items)
    if n == 0:
        return {}
    if n == 1:
        return {items[0][0]: 0.5}
    svals = sorted(v for _, v in items)
    result = {}
    for k, v in items:
        lo = bisect.bisect_left(svals, v)
        hi = bisect.bisect_right(svals, v)
        avg_rank = (lo + hi - 1) / 2.0          # 동일값은 동일 백분위
        p = avg_rank / (n - 1)
        result[k] = round(1.0 - p if invert else p, 4)
    return result

# 학교도서관 취약 복합점수 = 이용(0.40) + 콘텐츠(0.35) + 투자(0.25).
# 세 지표 모두 우편향이라 순위백분위(이상치 면역)로 정규화, 낮을수록 취약(invert).
W_SL_USE, W_SL_BOOK, W_SL_BUDGET = 0.40, 0.35, 0.25
use_norm    = percentile_rank_normalize(dict(adm_median_loans_per_student), invert=True)
book_norm   = percentile_rank_normalize(dict(adm_median_books_per_student), invert=True)
budget_norm = percentile_rank_normalize(dict(adm_median_budget_per_student), invert=True)

sl_adms = set(adm_median_loans_per_student) | set(adm_median_books_per_student) | set(adm_median_budget_per_student)
loan_score = {}
for adm in sl_adms:
    parts, wsum = [], 0.0
    if adm in use_norm:
        parts.append(use_norm[adm] * W_SL_USE);      wsum += W_SL_USE
    if adm in book_norm:
        parts.append(book_norm[adm] * W_SL_BOOK);    wsum += W_SL_BOOK
    if adm in budget_norm:
        parts.append(budget_norm[adm] * W_SL_BUDGET); wsum += W_SL_BUDGET
    loan_score[adm] = round(sum(parts) / wsum, 4) if wsum > 0 else 0.5
print(f"  학교도서관 복합점수 산출 읍면동: {len(loan_score)}개 "
      f"(이용{W_SL_USE}+장서{W_SL_BOOK}+예산{W_SL_BUDGET})")

# 다문화 복합 취약 점수 (2개 하위지표)
# ① 다문화 가구 비율 (수요측) — 높을수록 취약
# ② 다문화 서비스 미도달률 = 1 - (서비스이용수/봉사대상다문화수) (공급측) — 높을수록 취약
mc_ratio_map  = {}
mc_unreached_map = {}
for adm in all_adm_cds:
    mc = adm_mc_count.get(adm, 0)
    hh = adm_households.get(adm, 0)
    mc_ratio_map[adm] = mc / hh if hh > 0 else 0.0
    svc   = adm_mc_service.get(adm, 0)
    s_tgt = adm_mc_service_target.get(adm, 0)
    # 도서관이 없으면 서비스 미도달 = 최대(1.0), 도서관 있으면 실제 도달률로 계산
    if adm_publib.get(adm, 0) == 0:
        mc_unreached_map[adm] = 1.0
    elif s_tgt > 0:
        mc_unreached_map[adm] = max(0.0, 1.0 - svc / s_tgt)
    else:
        mc_unreached_map[adm] = 0.5  # 데이터 없음 → 중립

mc_ratio_norm    = min_max_normalize(mc_ratio_map,    invert=False)
mc_unreached_norm= min_max_normalize(mc_unreached_map,invert=False)

W_MC_RATIO    = 0.50
W_MC_UNREACHED= 0.50
mc_score = {
    adm: round(mc_ratio_norm[adm]*W_MC_RATIO + mc_unreached_norm[adm]*W_MC_UNREACHED, 4)
    for adm in all_adm_cds
}

# 복합 접근성 지표 (5개 하위지표)
# ① 도서관수 / 학교수                      (0.15) — 물리적 접근성
# ② 어린이 대출자수 / 봉사대상어린이         (0.25) — 실질 대출 이용률
# ③ 어린이 장서수 / 학생수                  (0.20) — 콘텐츠 풍부도
# ④ 독서프로그램 참가자수 / 학생수           (0.20) — 프로그램 접근성
# ⑤ 어린이 회원 등록자수 / 봉사대상어린이    (0.20) — 도서관 연결도(등록률)

adm_access_lib_ratio      = {}
adm_access_borrower_ratio = {}
adm_access_collection     = {}
adm_access_program        = {}
adm_access_member_ratio   = {}

for adm_cd in all_adm_cds:
    n_school  = school_count_by_adm.get(adm_cd, 0)
    n_student = adm_students.get(adm_cd, 0)
    denom_s   = max(n_student, 1)
    denom_sc  = max(n_school, 1)
    target    = max(adm_child_target.get(adm_cd, 0), 1)
    adm_access_lib_ratio[adm_cd]      = adm_publib.get(adm_cd, 0) / denom_sc
    adm_access_borrower_ratio[adm_cd] = adm_child_borrowers.get(adm_cd, 0) / target
    adm_access_collection[adm_cd]     = adm_child_collection.get(adm_cd, 0) / denom_s
    adm_access_program[adm_cd]        = adm_reading_program.get(adm_cd, 0) / denom_s
    adm_access_member_ratio[adm_cd]   = adm_child_members.get(adm_cd, 0) / target

lib_norm       = min_max_normalize(adm_access_lib_ratio,      invert=True)
borrower_norm  = min_max_normalize(adm_access_borrower_ratio, invert=True)
collect_norm   = min_max_normalize(adm_access_collection,     invert=True)
program_norm   = min_max_normalize(adm_access_program,        invert=True)
member_norm    = min_max_normalize(adm_access_member_ratio,   invert=True)

W_LIB_CNT      = 0.15
W_LIB_BORROWER = 0.25
W_LIB_COLLECT  = 0.20
W_LIB_PROGRAM  = 0.20
W_LIB_MEMBER   = 0.20
access_score = {
    adm: round(
        lib_norm[adm]      * W_LIB_CNT
      + borrower_norm[adm] * W_LIB_BORROWER
      + collect_norm[adm]  * W_LIB_COLLECT
      + program_norm[adm]  * W_LIB_PROGRAM
      + member_norm[adm]   * W_LIB_MEMBER,
        4)
    for adm in all_adm_cds
}

# 가중치: 학교도서관 비중 0.35→0.25 감소(지표 신뢰성 반영), 다문화 복합지표 0.30→0.40 증가
W_ACCESS = 0.35
W_LOAN   = 0.25
W_MC     = 0.40

results = {}
for adm_cd in sorted(all_adm_cds):
    props = adm_to_props[adm_cd]
    n_school = school_count_by_adm.get(adm_cd, 0)
    has_school = n_school > 0

    a = access_score.get(adm_cd, 0.5)
    l = loan_score.get(adm_cd, 0.5) if has_school else 1.0
    m = mc_score.get(adm_cd, 0)

    risk = round(a * W_ACCESS + l * W_LOAN + m * W_MC, 4)

    results[adm_cd] = {
        'adm_cd': adm_cd,
        'adm_nm': props['ADM_NM'],
        'sgg': props['sgg'],
        'risk_index': risk,
        'access_score': a,
        'loan_score': l,
        'multicultural_score': m,
        # 공공도서관 상세
        'publib_count': adm_publib.get(adm_cd, 0),
        'publib_child_borrowers': adm_child_borrowers.get(adm_cd, 0),
        'publib_child_target': adm_child_target.get(adm_cd, 0),
        'publib_child_collection': adm_child_collection.get(adm_cd, 0),
        'publib_reading_program': adm_reading_program.get(adm_cd, 0),
        'publib_child_loans': adm_child_loans.get(adm_cd, 0),       # 참고용
        'publib_service_area': int(adm_service_area.get(adm_cd, 0)), # 참고용
        # 학교도서관 상세
        'school_count': n_school,
        'total_students': adm_students.get(adm_cd, 0),
        'median_loans_per_student': round(adm_median_loans_per_student.get(adm_cd, 0), 2),
        'median_books_per_student': round(adm_median_books_per_student.get(adm_cd, 0), 1),
        'median_budget_per_student': round(adm_median_budget_per_student.get(adm_cd, 0)),
        # 다문화
        'multicultural_count': adm_mc_count.get(adm_cd, 0),
        'multicultural_ratio': round(mc_ratio_map.get(adm_cd, 0), 4),
        'multicultural_service': adm_mc_service.get(adm_cd, 0),
        'multicultural_unreached': round(mc_unreached_map.get(adm_cd, 0), 4),
        'has_school': has_school,
    }

# 상위 취약 읍면동 출력
ranked = sorted(results.values(), key=lambda x: x['risk_index'], reverse=True)
print(f"\n📋 취약지수 상위 20개 읍면동 (학교 있는 곳):")
school_ranked = [r for r in ranked if r['has_school']]
print(f"{'순위':>4} {'읍면동':<12} {'시군구':<10} {'취약지수':>8} {'접근성':>6} {'대출':>6} {'다문화':>6} {'학교':>4}")
print("-" * 70)
for i, r in enumerate(school_ranked[:20], 1):
    print(f"{i:>4} {r['adm_nm']:<12} {r['sgg']:<10} {r['risk_index']:>8.3f} "
          f"{r['access_score']:>6.3f} {r['loan_score']:>6.3f} {r['multicultural_score']:>6.3f} "
          f"{r['school_count']:>4}")

# ============================================================
# 7. JSON 저장
# ============================================================
output = {
    'metadata': {
        'title': '전북 문해력 인프라 취약지수(초등)',
        'year': 2024,
        'unit': '읍면동',
        'weights': {'access': W_ACCESS, 'loan': W_LOAN, 'multicultural': W_MC},
        'description': '공공도서관접근성복합(0.35)[도서관수0.15+어린이대출자비율0.25+장서수0.20+독서프로그램0.20+회원등록률0.20] + 학교도서관복합(0.25)[1인당대출0.40+1인당장서0.35+1인당자료구입예산0.25, 순위백분위 정규화] + 다문화복합(0.40)[가구비율0.50+서비스미도달률0.50]'
    },
    'data': results
}

out_path = os.path.join(DATA_DIR, 'used', 'risk_data.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ 저장 완료: {out_path}")
print(f"   총 읍면동: {len(results)}개 (학교 있는 곳: {sum(1 for r in results.values() if r['has_school'])}개)")
