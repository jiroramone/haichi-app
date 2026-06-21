import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
import os
import io
import uuid
import unicodedata
import logging
from datetime import date

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# GitHub自動取得設定
# フォルダ構成:
#   today/  ... 当日出馬表CSV
#   prev/   ... 前日結果CSV
#   train/  ... 坂路調教CSV
#   data/   ... 過去履歴DB CSV
# ============================================================
GITHUB_REPO   = "jiroramone/haichi-app"
GITHUB_BRANCH = "main"

GITHUB_FOLDER_TODAY  = "today"
GITHUB_FOLDER_PREV   = "prev"
GITHUB_FOLDER_TRAIN  = "train"
GITHUB_FOLDER_DATA   = "data"

MASTER_CSV_CANDIDATES = [
    r"C:\\Users\\keita\\Desktop\\配置馬券AC\\2023-2026.csv",
    "2023-2026.csv",
    r"C:\\Users\\keita\\Desktop\\配置馬券AC\\2024-2026タイム付き.csv",
    "2024-2026タイム付き.csv",
    "2025-2026 タイム付き.csv",
]

JRA_VENUES = {"札幌":"01","函館":"02","福島":"03","新潟":"04","東京":"05",
              "中山":"06","中京":"07","京都":"08","阪神":"09","小倉":"10"}

st.set_page_config(page_title="🏇 配置馬券 スマホ版", layout="centered", initial_sidebar_state="collapsed")

# ============================================================
# スマホ最適化CSS
# ============================================================
st.markdown("""
<style>
/* ベース */
html, body { font-size: 16px !important; }
.block-container { padding: 0.5rem 0.6rem 2rem !important; }

/* レース選択ボタン */
.race-btn button {
    font-size: 18px !important;
    font-weight: bold !important;
    height: 52px !important;
    border-radius: 10px !important;
}

/* 馬カード */
.m-card {
    background: #fff;
    border-radius: 14px;
    border: 1.5px solid #e0e0e0;
    padding: 14px 16px;
    margin-bottom: 10px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.07);
}
.m-card.honmei  { border-color:#FF5722; background:#FBE9E7; }
.m-card.taikou  { border-color:#2196F3; background:#E3F2FD; }
.m-card.tanana  { border-color:#4CAF50; background:#E8F5E9; }
.m-card.renka   { border-color:#FFC107; background:#FFF8E1; }
.m-card.hoshi   { border-color:#9C27B0; background:#F3E5F5; }
.m-card.keshi   { opacity:0.4; filter:grayscale(80%); }

.m-horse-num  { font-size:22px; font-weight:900; color:#333; }
.m-horse-name { font-size:20px; font-weight:800; color:#111; margin-left:8px; }
.m-odds       { font-size:17px; font-weight:700; color:#E65100; margin-left:auto; }
.m-jockey     { font-size:15px; color:#555; margin-top:4px; }
.m-score      { font-size:14px; color:#1565C0; margin-top:2px; }
.m-badge      { display:inline-block; font-size:12px; padding:2px 8px;
                border-radius:20px; margin-right:4px; margin-top:4px;
                font-weight:bold; }
.badge-ao   { background:#E3F2FD; color:#1565C0; }
.badge-ki   { background:#FFF8E1; color:#E65100; }
.badge-owabi{ background:#F3E5F5; color:#6A1B9A; }

/* 印ボタン */
.mark-row button {
    font-size: 20px !important;
    height: 48px !important;
    border-radius: 8px !important;
    font-weight: bold !important;
}

/* タッチ操作しやすい下部ナビ */
.bottom-nav {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #fff;
    border-top: 1px solid #ddd;
    display: flex;
    justify-content: space-around;
    padding: 8px 0 12px;
    z-index: 9999;
}
.bottom-nav a {
    text-align: center; font-size: 11px; color: #555;
    text-decoration: none; display: flex; flex-direction: column;
    align-items: center; gap: 2px;
}
.bottom-nav a span { font-size: 22px; }

/* ページ余白（下部ナビ分） */
.main .block-container { padding-bottom: 80px !important; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# セッション初期化
# ============================================================
_SESSION_DEFAULTS = {
    "saved_chaku": {}, "ignored_horses": {}, "user_markers": {},
    "partner_cache": {}, "fully_processed_df": pd.DataFrame(),
    "cached_owabi_riders": set(), "m_selected_venue": None,
    "m_selected_race": None, "m_expand_card": set(),
}
for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ============================================================
# ロジック関数（PC版と共通）
# ============================================================
def parse_date(val):
    val_str = str(val)
    match = re.search(r'\d+', val_str)
    if match:
        nums = match.group()
        if len(nums) == 6:
            return pd.to_datetime(nums, format='%y%m%d', errors='coerce')
        elif len(nums) >= 8:
            return pd.to_datetime(nums[:8], format='%Y%m%d', errors='coerce')
    return pd.NaT

def clean_horse_name(name):
    if pd.isna(name) or name is None:
        return ""
    name_str = unicodedata.normalize('NFKC', str(name))
    name_str = name_str.strip().replace(" ", "").replace(" ", "").replace("$", "").replace("*", "").replace("＊", "")
    name_str = re.sub(r'[\(（\[［〇○□][外地父抽][\)）\]］]?', '', name_str)
    name_str = re.sub(r'^[〇○□]+', '', name_str)
    return name_str

def normalize_rank(val):
    norm_str = unicodedata.normalize('NFKC', str(val))
    match = re.search(r'\d+', norm_str)
    if match:
        return int(match.group())
    return pd.NA

def clean_time_diff(val):
    if pd.isna(val) or str(val).strip() == "":
        return np.nan
    cleaned = str(val).replace('秒', '').replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return np.nan

def format_lap_time(val):
    try:
        return f"{float(val):.1f}"
    except (ValueError, TypeError):
        return "-"

# 【改修】CSVパス候補リスト（集中管理）
MASTER_CSV_CANDIDATES = [
    r"C:\Users\keita\Desktop\配置馬券AC\2023-2026.csv",
    "2023-2026.csv",
    r"C:\Users\keita\Desktop\配置馬券AC\2024-2026タイム付き.csv",
    "2024-2026タイム付き.csv",
    r"C:\Users\keita\Desktop\配置馬券AC\2024-2026pci付き.csv",
    "2025-2026 タイム付き.csv",
]

def _read_csv_with_encoding(filepath_or_buffer):
    """【改修】UTF-8 → CP932 の順でCSV読み込みを試みる共通関数"""
    try:
        return pd.read_csv(filepath_or_buffer, encoding='utf-8')
    except UnicodeDecodeError:
        if hasattr(filepath_or_buffer, 'seek'):
            filepath_or_buffer.seek(0)
        return pd.read_csv(filepath_or_buffer, encoding='cp932')

@st.cache_resource
def get_master_history_data():
    """【改修】候補リストから最初に存在するCSVを自動選択"""
    filepath = None
    for candidate in MASTER_CSV_CANDIDATES:
        if os.path.exists(candidate):
            filepath = candidate
            break
    if filepath is None:
        return None
    try:
        df = _read_csv_with_encoding(filepath)
        df['date'] = df['日付'].apply(parse_date)
        if 'レースID(新)' in df.columns:
            df['race_id'] = df['レースID(新)'].astype(str).str.strip().str[:-2]
        else:
            df['race_id'] = df['日付'].astype(str) + df['場所'].astype(str) + df['Ｒ'].astype(str)
        df['rank'] = df['着順'].apply(normalize_rank)
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = df.dropna(subset=['date']).sort_values(by=['馬名', 'date']).reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f'get_master_history_data: 読み込み失敗 ({filepath}): {e}')
        return None

def get_manual_history_data(file_bytes_content):
    f = io.BytesIO(file_bytes_content)
    try:
        try: 
            df = pd.read_csv(f, encoding='utf-8')
        except UnicodeDecodeError: 
            f.seek(0)
            df = pd.read_csv(f, encoding='cp932')
            
        df['date'] = df['日付'].apply(parse_date)
        if 'レースID(新)' in df.columns: 
            df['race_id'] = df['レースID(新)'].astype(str).str.strip().str[:-2]
        else: 
            df['race_id'] = df['日付'].astype(str) + df['場所'].astype(str) + df['Ｒ'].astype(str)
            
        df['rank'] = df['着順'].apply(normalize_rank)
        df['馬名'] = df['馬名'].apply(clean_horse_name)
        df = df.dropna(subset=['date']).sort_values(by=['馬名', 'date']).reset_index(drop=True)
        return df
    except Exception:
        return None

def _build_history_index(history_df):
    """馬名・race_id で高速検索するためのインデックスを構築。"""
    if history_df is None or history_df.empty:
        return {}, {}
    horse_idx  = {name: grp for name, grp in history_df.groupby('馬名', sort=False)}
    raceid_idx = {rid: grp  for rid,  grp in history_df.groupby('race_id', sort=False)}
    return horse_idx, raceid_idx

# -------------------------------------------------------------------------
# 黄金比能力判定
# -------------------------------------------------------------------------
def apply_performance_levels(curr_df, history_df, global_target_datetime):
    if curr_df.empty: 
        return curr_df
        
    time_diff_col = '着差' if history_df is not None and '着差' in history_df.columns else None
    leg_type_col = '脚質' if history_df is not None and '脚質' in history_df.columns else None
    horse_idx, raceid_idx = _build_history_index(history_df)
    results = []
    
    for idx, row in curr_df.iterrows():
        target_horse = clean_horse_name(row['馬名'])
        
        if '日付S' in curr_df.columns and pd.notna(row['日付S']): 
            target_datetime = pd.to_datetime(row['日付S'], errors='coerce')
        elif '日付' in curr_df.columns and pd.notna(row['日付']): 
            target_datetime = parse_date(row['日付'])
        else: 
            target_datetime = global_target_datetime
            
        if pd.isna(target_datetime) or target_datetime is pd.NaT: 
            target_datetime = global_target_datetime
            
        if horse_idx:
            _hdf = horse_idx.get(target_horse, pd.DataFrame())
            horse_history = _hdf[_hdf['date'] < target_datetime] if not _hdf.empty else pd.DataFrame()
        else:
            horse_history = pd.DataFrame()
        
        res_data = {
            '総合指数': 0.0, 'レベル点': 0.0, '自力点': 0.0, 'ボーナス減点': 0.0,
            '前走着順': np.nan, 'レース間隔': '-', '好走/次走あり': '0/0',
            '前走着差': '-', '前走脚質': '-', '長期休養フラグ': '-', '前走日付': 'データなし'
        }
        
        if not horse_history.empty:
            prev_race = horse_history.iloc[-1]
            prev_race_id = prev_race['race_id']
            prev_race_date = prev_race['date']
            rank_val = prev_race['rank']
            
            if time_diff_col and pd.notna(prev_race[time_diff_col]):
                time_diff_num = clean_time_diff(prev_race[time_diff_col])
            else:
                time_diff_num = None
                
            leg_val = prev_race[leg_type_col] if leg_type_col and pd.notna(prev_race[leg_type_col]) else None
            
            delta_days = (target_datetime - prev_race_date).days
            if delta_days <= 8: 
                interval_str = "連闘"
                interval_weeks = 1
            else: 
                naka_shu = (delta_days - 6) // 7
                interval_str = f"中{naka_shu}週"
                interval_weeks = naka_shu + 1
            
            _rdf = raceid_idx.get(prev_race_id, pd.DataFrame())
            rivals = _rdf[_rdf['馬名'] != target_horse] if not _rdf.empty else pd.DataFrame()
                
            rival_names = rivals['馬名'].unique() if not rivals.empty else []
            field_size = len(rival_names) + 1
            
            # 【高速化】ライバルの次走をまとめてクエリ（forループ → isin+groupbyでベクトル化）
            rival_future_all = history_df[
                history_df['馬名'].isin(rival_names) &
                (history_df['date'] > prev_race_date) &
                (history_df['date'] < target_datetime)
            ] if len(rival_names) > 0 else pd.DataFrame()

            rival_next = (rival_future_all
                          .sort_values('date')
                          .groupby('馬名', sort=False)
                          .first()
                          .reset_index()) if not rival_future_all.empty else pd.DataFrame()

            next_race_found   = len(rival_next)
            rival_top1_count  = int((rival_next['rank'] == 1).sum()) if not rival_next.empty else 0
            rival_top2_count  = int((rival_next['rank'] <= 2).sum()) if not rival_next.empty else 0
            high_achievers    = int((rival_next['rank'] <= 3).sum()) if not rival_next.empty else 0
            rival_top5_count  = int((rival_next['rank'] <= 5).sum()) if not rival_next.empty else 0

            rival_rank_map      = rivals.set_index('馬名')['rank'].to_dict() if not rivals.empty else {}
            top1_names          = rival_next[rival_next['rank'] == 1]['馬名'].tolist() if not rival_next.empty else []
            top3_names          = rival_next[rival_next['rank'] <= 3]['馬名'].tolist() if not rival_next.empty else []
            top5_names          = rival_next[rival_next['rank'] <= 5]['馬名'].tolist() if not rival_next.empty else []
            top1_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top1_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            high_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top3_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            top5_achiever_ranks = sorted([int(rival_rank_map[n]) for n in top5_names if n in rival_rank_map and pd.notna(rival_rank_map[n])])
            
            if top1_achiever_ranks:
                top1_text = f"🥇1着:{rival_top1_count}頭(前{','.join(map(str, sorted(top1_achiever_ranks)))}着)"
            else:
                top1_text = f"🥇1着:{rival_top1_count}頭"

            if high_achiever_ranks:
                top3_text = f"🥉3着内:{high_achievers}頭(前{','.join(map(str, sorted(high_achiever_ranks)))}着)"
            else:
                top3_text = f"🥉3着内:{high_achievers}頭"

            top5_text = f"🎖️5着内:{rival_top5_count}頭"

            hoso_text = f"【次走: {next_race_found}頭】 {top1_text} ｜ {top3_text} ｜ {top5_text}"

            flag_status = "-"
            try:
                age_val = None
                for c in ['年齢', '性齢', '馬齢', '齢']:
                    if c in row.index and pd.notna(row[c]) and str(row[c]).strip():
                        age_val = row[c]
                        break
                if not age_val:
                    for c in ['年齢', '性齢', '馬齢', '齢']:
                        if c in prev_race.index and pd.notna(prev_race[c]) and str(prev_race[c]).strip():
                            age_val = prev_race[c]
                            break
                            
                horse_age = 99
                if age_val:
                    age_match = re.search(r'\d+', str(age_val))
                    if age_match:
                        horse_age = int(age_match.group())
                
                race_class_parts = []
                for c in ['クラス名', 'クラス', '条件', '競走名', 'レース名']:
                    if c in row.index and pd.notna(row[c]):
                        race_class_parts.append(str(row[c]))
                
                race_class_str = unicodedata.normalize('NFKC', " ".join(race_class_parts))
                
                if not race_class_str.strip() or race_class_str.strip() in ['nan', 'None']: 
                    is_target_class = True
                else: 
                    is_target_class = any(c in race_class_str for c in ['未勝利', '1勝', '2勝', '3勝'])
                
                if delta_days >= 83 and (horse_age <= 5 or horse_age == 99) and is_target_class:
                    flag_status = f"休養(次走{next_race_found}頭中 ➡ 🥇1着:{rival_top1_count}頭 / 🥈連対:{rival_top2_count}頭 / 🎖️5着内:{rival_top5_count}頭)"
                    if rival_top5_count >= 8 and rival_top2_count >= 1: 
                        flag_status = f"🚩 注目(超Hレベル休養明け・次走{next_race_found}頭中 ➡ 🥇1着:{rival_top1_count}頭 / 🥈連対:{rival_top2_count}頭)"
            except Exception: 
                pass
            
            if next_race_found > 0:
                bayesian_rate = (high_achievers + 0.6) / (next_race_found + 3.0)
            else:
                bayesian_rate = (0.6 / 3.0)
                
            raw_score_a = min(50.0, bayesian_rate * 125) 
            
            if interval_weeks <= 8:
                decay_rate = 1.0
            else:
                decay_rate = max(0.4, 1.0 - ((interval_weeks - 8) * 0.025))
                
            if pd.notna(rank_val):
                rank_int = int(rank_val)
            else:
                rank_int = 99
            
            if rank_int <= 5:
                rank_discount = 1.0
            elif rank_int <= 9:
                rank_discount = 0.8
            else:
                rank_discount = 0.5
                
            score_a = raw_score_a * decay_rate * rank_discount
            
            if pd.notna(time_diff_num): 
                base_score_b = max(0.0, 50.0 - (time_diff_num * 20.0))
            else: 
                if rank_int == 1: base_score_b = 50.0
                elif rank_int == 2: base_score_b = 35.0
                elif rank_int == 3: base_score_b = 25.0
                elif rank_int <= 5: base_score_b = 15.0
                elif rank_int <= 9: base_score_b = 5.0
                else: base_score_b = 0.0
            
            rivals_count_safe = max(1, len(rival_names))
            reliability_ratio = min(1.0, next_race_found / rivals_count_safe)
            weight_multiplier = 1.0 + ((1.0 - reliability_ratio) * 0.5) 
            score_b = base_score_b * weight_multiplier
            
            score_c = max(0, field_size - 10) * 1.0
            
            if interval_str == "連闘" or interval_weeks >= 25:
                score_d = -10.0
            else:
                score_d = 0.0
            
            if pd.notna(leg_val):
                leg_str = str(leg_val).strip()
            else:
                leg_str = ""
                
            if "逃" in leg_str or "先" in leg_str or "1" in leg_str.split('-')[:2]: 
                score_c += 3.0
                
            lap_eval = row.get('ラップ評価', '-')
            if pd.notna(lap_eval):
                if '🌟激アツ: 5' in lap_eval: score_c += 8.0  
                elif '5 終い11秒台の加速' in lap_eval: score_c += 5.0
                elif '3 終い2F12秒台' in lap_eval: score_c += 3.0
                elif '1 終いのみ12秒台' in lap_eval: score_c += 1.0
                elif '終い2F11秒台まとめの減速' in lap_eval: score_c += 4.0  
                elif '🌟激アツ: 6' in lap_eval: score_c += 5.0  
                elif '6 2Fのみ11秒台の減速' in lap_eval: score_c += 2.0  
                elif '🚨危険(地雷)' in lap_eval: score_c -= 5.0  
                
            total_index = round(score_a + score_b + score_c + score_d, 1)
            
            if pd.notna(prev_race[time_diff_col]):
                prev_diff_str = f"{prev_race[time_diff_col]}秒"
            else:
                prev_diff_str = "-"
            
            res_data.update({
                '総合指数': total_index, 
                'レース間隔': interval_str, 
                '前走着順': rank_int if rank_int != 99 else None,
                '前走着差': prev_diff_str, 
                '前走脚質': leg_str if leg_str else "-",
                'レベル点': round(score_a, 1), 
                '自力点': round(score_b, 1), 
                'ボーナス減点': round(score_c + score_d, 1),
                '長期休養フラグ': flag_status, 
                '好走/次走あり': hoso_text, 
                '前走日付': prev_race_date.strftime('%Y-%m-%d')
            })
            
        results.append(res_data)
        
    res_df = pd.DataFrame(results)
    for col in res_df.columns: 
        curr_df[col] = res_df[col].values
        
    return curr_df

def escape_html(text: str) -> str:
    """【改修】HTMLインジェクション対策：特殊文字をエスケープ"""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))

def get_waku_info(umaban, tousu):
    if pd.isna(tousu) or tousu <= 0: 
        return 1, "#FFFFFF", "#000000", "#CCCCCC"
        
    tousu = int(tousu)
    umaban = int(umaban)
    
    if tousu <= 8: 
        waku = umaban
    else:
        base = tousu // 8
        rem = tousu % 8
        waku_sizes = [base] * 8
        for i in range(rem): 
            waku_sizes[7 - i] += 1
            
        cumulative = 0
        waku = 8
        for w_idx, size in enumerate(waku_sizes):
            cumulative += size
            if umaban <= cumulative: 
                waku = w_idx + 1
                break
                
    w_c = {
        1: ("#FFFFFF", "#000000", "#CCCCCC"), 
        2: ("#1A1A1A", "#FFFFFF", "#1A1A1A"), 
        3: ("#E53935", "#FFFFFF", "#E53935"), 
        4: ("#1E88E5", "#FFFFFF", "#1E88E5"), 
        5: ("#FDD835", "#000000", "#FDD835"), 
        6: ("#43A047", "#FFFFFF", "#43A047"), 
        7: ("#FB8C00", "#000000", "#FB8C00"), 
        8: ("#F06292", "#FFFFFF", "#F06292")
    }
    bg, fg, border = w_c.get(waku, ("#FFFFFF", "#000000", "#CCCCCC"))
    return waku, bg, fg, border

def has_previous_pair_race(pair_str, current_r_num, my_venue):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return False
        
    items = [x.strip() for x in str(pair_str).split(',') if x.strip()]
    for item in items:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            m_r = re.search(r'(\d+)R', race_key)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', race_key)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
        else:
            m_r = re.search(r'(\d+)R', item)
            if m_r:
                r_val = int(m_r.group(1))
                venue_part = re.sub(r'\d+R', '', item)
                is_same_venue = (not venue_part) or (venue_part == my_venue)
                if is_same_venue and r_val < current_r_num: 
                    return True
    return False

def split_by_comma_outside_parentheses(text):
    if not text: 
        return []
    items = []
    current = []
    depth = 0
    for char in text:
        if char == '(': 
            depth += 1
        elif char == ')': 
            depth -= 1
            
        if char == ',' and depth == 0: 
            items.append("".join(current).strip())
            current = []
        else: 
            current.append(char)
            
    if current: 
        items.append("".join(current).strip())
    return [x for x in items if x]

def format_condensed_pairs(pair_str):
    if not pair_str or pd.isna(pair_str) or str(pair_str).strip() in ["", "ー", "nan"]: 
        return ""
        
    race_map = {}
    for item in [x.strip() for x in str(pair_str).split(',') if x.strip()]:
        m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
        if m:
            race_key = m.group(1)
            sig_part = m.group(2)
            if race_key not in race_map: 
                race_map[race_key] = set()
            if sig_part:
                for s in sig_part.split(','):
                    if s.strip(): 
                        race_map[race_key].add(s.strip())
        else:
            if item not in race_map: 
                race_map[item] = set()
                
    def sort_key(x):
        race_name = x[0]
        venue = re.sub(r'\d+R', '', race_name)
        m_r = re.search(r'(\d+)R', race_name)
        r_num = int(m_r.group(1)) if m_r else 99
        return (venue, r_num)
        
    condensed_items = []
    for k, v in sorted(race_map.items(), key=sort_key):
        if v:
            condensed_items.append(f"{k}({','.join(sorted(list(v)))})")
        else:
            condensed_items.append(k)
            
    return ",".join(condensed_items)

def make_badge_html(label_type, text):
    if pd.isna(text) or text is None or str(text).strip() in ["", "ー"]: 
        return ""
        
    text_str = str(text).strip()
    bg = "#f0f0f0"
    color = "#333333"
    
    if "〇" in text_str: 
        bg = "#FFD2D2"
        color = "#800000"
    elif "▲" in text_str: 
        bg = "#FFE4C4"
        color = "#8B4513"
    elif "△" in text_str: 
        bg = "#FFF9C4"
        color = "#6D4C41"
    elif "◆" in text_str: 
        bg = "#D6E4FF"
        color = "#002D80"
    elif "✖" in text_str: 
        bg = "#ECEFF1"
        color = "#37474F"
    elif "✨🟦" in text_str: 
        bg = "#E3F2FD"
        color = "#0D47A1"
    elif "🔄" in text_str: 
        bg = "#F3E5F5"
        color = "#4A148C"
    
    if label_type == "騎手":
        icon = "👤"
    elif label_type == "調教":
        icon = "🏠"
    elif label_type == "馬主":
        icon = "👑"
    else:
        icon = "🔖"
        
    return f"""
    <div style="background-color: {bg}; color: {color}; font-size: 13px; font-weight: bold; padding: 6px 10px; border-radius: 6px; margin-top: 4px; display: flex; align-items: center; gap: 8px; border: 1px solid {bg};">
        <span>{icon}</span><span>{text_str}</span>
    </div>
    """


def preprocess_and_calculate_haichi(df):
    if df.empty: 
        return df
        
    df = df.reset_index(drop=True)
    
    if 'Ｒ' in df.columns: 
        df['Ｒ'] = df['Ｒ'].astype(str).str.extract(r'(\d+)')[0].astype(int)
        
    if '場所' in df.columns: 
        df['場所'] = df['場所'].astype(str).str.extract(r'([^\d\s\(\)]+)')[0].str.strip()
        
    for col in ['騎手', '調教師', '馬主(最新/仮想)']:
        if col in df.columns: 
            df[col] = df[col].apply(clean_horse_name)
            
    df['頭数'] = df.groupby(['日付S', '場所', 'Ｒ'])['馬番'].transform('max')
    df['正番'] = df['馬番'].astype(int)
    df['逆番'] = df['頭数'].astype(int) - df['正番'] + 1
    df['正循環'] = df['正番'] + df['頭数'].astype(int)
    df['逆循環'] = df['逆番'] + df['頭数'].astype(int)
    
    return df.sort_values(by=['日付S', '場所', 'Ｒ', '馬番']).reset_index(drop=True)

def calculate_haichi_features(df):
    df = df.copy()
    if df.empty: 
        return df
        
    df['is_blue_jockey'] = df['騎手_青塗'].astype(float)
    df['next_to_blue'] = 0.0
    df['is_symmetry'] = 0.0
    df['next_to_symmetry'] = 0.0
    
    for (date, loc, r), group in df.groupby(['日付S', '場所', 'Ｒ']):
        blue_nums = group[group['is_blue_jockey'] == 1.0]['馬番'].tolist()
        for b_num in blue_nums:
            target = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r) & ((df['馬番']==b_num+1)|(df['馬番']==b_num-1))
            df.loc[target, 'next_to_blue'] = 1.0
            
        for t_col in ['調教師']:
            if t_col not in df.columns: 
                continue
                
            for name, t_group in group.groupby(t_col):
                name_str = str(name).strip()
                if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                    continue
                    
                if len(t_group) > 1:
                    for idx, row in t_group.iterrows():
                        other_gyaku = t_group[t_group['馬番'] != row['馬番']]['逆番'].tolist()
                        if row['正番'] in other_gyaku:
                            target_base = (df['日付S']==date) & (df['場所']==loc) & (df['Ｒ']==r)
                            df.loc[target_base & (df['馬番']==row['馬番']), 'is_symmetry'] = 1.0
                            
                            target_next = target_base & ((df['馬番']==row['馬番']+1)|(df['馬番']==row['馬番']-1)) & (df['is_symmetry']==0)
                            df.loc[target_next, 'next_to_symmetry'] = 1.0
                            
    return df

def judge_yellow_and_pairs(combined_df, target_col='騎手'):
    combined_df[f'{target_col}_黄塗'] = False
    combined_df[f'{target_col}_ペア'] = ""
    combined_df[f'{target_col}_厩舎同ペア'] = False
    
    if combined_df.empty or target_col not in combined_df.columns: 
        return combined_df

    pair_map = {
        ('正番', '正番'): 'A', ('正番', '逆番'): 'B', ('正番', '正循環'): 'C', ('正番', '逆循環'): 'D', 
        ('逆番', '正番'): 'E', ('逆番', '逆番'): 'F', ('逆番', '正循環'): 'G', ('逆番', '逆循環'): 'H', 
        ('正循環', '正番'): 'I', ('正循環', '逆番'): 'J', ('正循環', '正循環'): 'K', ('正循環', '逆循環'): 'L', 
        ('逆循環', '正番'): 'M', ('逆循環', '逆番'): 'N', ('逆循環', '正循環'): 'O', ('逆循環', '逆循環'): 'P'
    }

    for (date, loc), loc_group in combined_df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            race_nums = sorted(group['Ｒ'].unique())
            if len(race_nums) < 2:
                continue
                
            for i in range(len(race_nums) - 1):
                prev_r = race_nums[i]
                curr_r = race_nums[i + 1]
                
                prev_rows = group[group['Ｒ'] == prev_r]
                curr_rows = group[group['Ｒ'] == curr_r]
                
                for idx_prev, prev_row in prev_rows.iterrows():
                    for idx_curr, curr_row in curr_rows.iterrows():
                        match_found = False
                        detected_pairs_curr = []
                        detected_pairs_prev = []
                        
                        info_for_curr = f"{prev_r}R"
                        info_for_prev = f"{curr_r}R"
                        
                        is_stable_match = False
                        if '騎手' in prev_row and '調教師' in prev_row:
                            if prev_row['騎手'] == curr_row['騎手'] and prev_row['調教師'] == curr_row['調教師']:
                                is_stable_match = True
                        
                        for p1 in ['正番', '逆番', '正循環', '逆循環']:
                            for p2 in ['正番', '逆番', '正循環', '逆循環']:
                                if prev_row[p1] == curr_row[p2]:
                                    match_found = True
                                    sig = pair_map[(p1, p2)]
                                    detected_pairs_curr.append(f"{info_for_curr}({sig})")
                                    detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        p_prev = prev_row['正番']
                        p_curr = curr_row['正番']
                        if (p_prev % 10 == p_curr % 10) and (p_prev != p_curr):
                            match_found = True
                            sig = 'Q' if p_prev < p_curr else 'R'
                            detected_pairs_curr.append(f"{info_for_curr}({sig})")
                            detected_pairs_prev.append(f"{info_for_prev}({sig})")
                        
                        if match_found:
                            combined_df.at[idx_curr, f'{target_col}_黄塗'] = True
                            combined_df.at[idx_prev, f'{target_col}_黄塗'] = True
                            
                            existing_curr = combined_df.at[idx_curr, f'{target_col}_ペア']
                            ex_list_curr = [x.strip() for x in existing_curr.split(',')] if existing_curr else []
                            for item in set(detected_pairs_curr):
                                if item not in ex_list_curr:
                                    ex_list_curr.append(item)
                            combined_df.at[idx_curr, f'{target_col}_ペア'] = ",".join(ex_list_curr)
                            
                            existing_prev = combined_df.at[idx_prev, f'{target_col}_ペア']
                            ex_list_prev = [x.strip() for x in existing_prev.split(',')] if existing_prev else []
                            for item in set(detected_pairs_prev):
                                if item not in ex_list_prev:
                                    ex_list_prev.append(item)
                            combined_df.at[idx_prev, f'{target_col}_ペア'] = ",".join(ex_list_prev)
                            
                            if is_stable_match: 
                                combined_df.at[idx_curr, f'{target_col}_厩舎同ペア'] = True
                                combined_df.at[idx_prev, f'{target_col}_厩舎同ペア'] = True

    return combined_df

def judge_blue_coating(df, target_col='騎手'):
    df[f'{target_col}_青塗'] = False
    blue_names = set()
    
    if df.empty or target_col not in df.columns: 
        return df, blue_names
        
    for (date, loc), loc_group in df.groupby(['日付S', '場所']):
        for name, group in loc_group.groupby(target_col):
            name_str = str(name).strip()
            if not name_str or name_str in ["", "ー", "nan", "None", "不明"]: 
                continue
            
            if group['Ｒ'].nunique() >= 2:
                for p in ['正番', '逆番', '正循環', '逆循環']:
                    if group[p].nunique() == 1:
                        df.loc[group.index, f'{target_col}_青塗'] = True
                        blue_names.add((date, name))
                        break 
                        
    return df, blue_names

def extract_owabi_riders(prev_df):
    owabi_riders = set()
    if prev_df.empty or '着順' not in prev_df.columns: 
        return owabi_riders
        
    prev_df, blue_date_riders = judge_blue_coating(prev_df, target_col='騎手')
    
    for date, rider in blue_date_riders:
        rider_races = prev_df[(prev_df['日付S'] == date) & (prev_df['騎手'] == rider)]
        has_hit = False
        for chaku in rider_races['着順']:
            try:
                chaku_int = int(float(str(chaku).strip()))
                if chaku_int in [1, 2, 3]: 
                    has_hit = True
                    break
            except Exception: 
                continue
                
        if not has_hit: 
            owabi_riders.add(rider)
            
    return owabi_riders

def calculate_placement_points(row):
    pts = 0.0
    if row.get('騎手_青塗') or row.get('調教師_青塗') or row.get('馬主(最新/仮想)_青塗', False): 
        pts += 3.0
    elif row.get('騎手_黄塗') or row.get('調教師_黄塗') or row.get('馬主(最新/仮想)_黄塗', False): 
        pts += 1.0
        
    if row.get('騎手_厩舎同ペア') or row.get('調教師_厩舎同ペア'): 
        pts += 2.0
        
    pair_str = str(row.get('騎手_ペア', '')) + str(row.get('調教師_ペア', '')) + str(row.get('馬主(最新/仮想)_ペア', ''))
    if any(p in pair_str for p in ['(C)', '(D)', '(G)', '(H)']): 
        pts += 1.5
        
    if row.get('is_symmetry', 0.0) == 1.0: 
        pts += 1.5
    elif row.get('next_to_symmetry', 0.0) == 1.0: 
        pts += 0.5
        
    odds_val = row.get('temp_odds', 0.0)
    if 10.0 <= odds_val < 20.0: 
        pts += 1.5
    elif 1.0 < odds_val < 50.0: 
        pts += 0.5
    elif odds_val >= 100.0: 
        pts -= 1.0
        
    return round(pts, 1)

def find_all_pair_partners_detailed(row, full_df):
    r_num = int(row['Ｒ'])
    my_venue = row['場所']
    date = row['日付S']
    partners_info = []
    targets = []
    
    if pd.notnull(row.get('騎手')): 
        targets.append(('騎手', row.get('騎手'), '騎手_ペア', 0))
    if pd.notnull(row.get('調教師')): 
        targets.append(('調教師', row.get('調教師'), '調教師_ペア', 1))
    if '馬主(最新/仮想)' in row.index and pd.notnull(row.get('馬主(最新/仮想)')): 
        targets.append(('馬主(最新/仮想)', row.get('馬主(最新/仮想)', 'ー'), '馬主(最新/仮想)_ペア', 2))
        
    for col_name, val, pair_col, cat_idx in targets:
        val_str = str(val).strip()
        if not val_str or val_str in ["", "ー", "nan", "None", "不明"]: 
            continue
            
        pair_text = str(row.get(pair_col, ''))
        if not pair_text or pair_text == "nan" or pair_text == "ー": 
            continue
            
        for item in split_by_comma_outside_parentheses(pair_text):
            m = re.match(r'^([^\s\(\)]+)(?:\(([^)]+)\))?$', item)
            if m:
                race_key = m.group(1)
                pair_sig = f" ({m.group(2)})" if m.group(2) else ""
                m_r = re.search(r'(\d+)R', race_key)
                if m_r:
                    tgt_r_int = int(m_r.group(1))
                    tgt_venue_part = re.sub(r'\d+R', '', race_key)
                    tgt_venue = tgt_venue_part if tgt_venue_part else my_venue
                    
                    match_rows = full_df[
                        (full_df['日付S'] == date) & 
                        (full_df['場所'] == tgt_venue) & 
                        (full_df['Ｒ'] == tgt_r_int) & 
                        (full_df[col_name] == val) & 
                        ~((full_df['場所'] == my_venue) & (full_df['Ｒ'] == r_num) & (full_df['馬番'] == row['馬番']))
                    ]
                
                    for _, m_row in match_rows.iterrows():
                        m_odds = m_row.get('オッズ')
                        m_pop = m_row.get('人気')
                        m_num = int(m_row.get('馬番'))
                        m_name = m_row.get('馬name', m_row.get('馬名', '不明'))
                        
                        c1 = st.session_state['saved_chaku'].get(f"c1_{tgt_venue}_{tgt_r_int}")
                        c2 = st.session_state['saved_chaku'].get(f"c2_{tgt_venue}_{tgt_r_int}")
                        c3 = st.session_state['saved_chaku'].get(f"c3_{tgt_venue}_{tgt_r_int}")
                        
                        chaku_status = "未確定"
                        if c1 is not None or c2 is not None or c3 is not None:
                            if m_num == c1: 
                                chaku_status = "<span style='color:#FFD700; font-weight:bold;'>🏆 1着好走</span>"
                            elif m_num == c2: 
                                chaku_status = "<span style='color:#C0C0C0; font-weight:bold;'>🥈 2着好走</span>"
                            elif m_num == c3: 
                                chaku_status = "<span style='color:#CD7F32; font-weight:bold;'>🥉 3着好走</span>"
                            else: 
                                chaku_status = "<span style='color:#888;'>凡走(4着以下)</span>"
                        
                        odds_txt = f"単勝 {m_odds}倍" if pd.notnull(m_odds) and str(m_odds).strip() not in ["", "nan"] else "単勝 未取得"
                        pop_txt = f"{int(m_pop)}人気" if pd.notnull(m_pop) and str(m_pop).strip() not in ["", "nan"] else "未設定"
                        
                        if col_name == "騎手":
                            category_label = "騎手"
                        elif col_name == "調教師":
                            category_label = "調教"
                        else:
                            category_label = "馬主"
                            
                        venue_label = f"{tgt_venue}" if tgt_venue != my_venue else ""
                        formatted_str = f"🔗 {venue_label}{tgt_r_int}R {m_num}番 {m_name} ({category_label}{pair_sig}) 【{odds_txt} / {pop_txt} / {chaku_status}】"
                        
                        partners_info.append((cat_idx, tgt_venue, tgt_r_int, m_num, formatted_str))
                    
    unique_partners = list(set(partners_info))
    unique_partners.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    
    result_list = []
    for item in unique_partners:
        result_list.append(item[4])
        
    return result_list

# -------------------------------------------------------------------------
# 🌟 カード描画用の共通関数 (1頭分)
# -------------------------------------------------------------------------
def render_single_horse_card(row_data, selected_venue, curr_df):
    r_num = int(row_data['Ｒ'])
    num_val = int(row_data['馬番']) if pd.notnull(row_data['馬番']) else 0
    name_val = row_data.get('馬name', row_data.get('馬名', '不明'))
    tousu_val = row_data.get('頭数', 16)
    w_num, waku_bg, waku_fg, waku_border = get_waku_info(num_val, tousu_val)
    
    raw_points = row_data.get('配置ポイント', 0.0)
    star_count = min(5, max(1, int(raw_points // 1.5))) if raw_points > 0 else 0
    stars = "★" * star_count if star_count > 0 else ""
    
    horse_key = f"{selected_venue}_{r_num}_{num_val}"
    marker_val = st.session_state['user_markers'].get(horse_key, "未設定")
    
    card_class = "game-card-container"
    if marker_val == "◎": 
        card_class += " active-honmei"
    elif marker_val == "○": 
        card_class += " active-taikou"
    elif marker_val == "▲": 
        card_class += " active-tanana"
    elif marker_val == "△": 
        card_class += " active-renka"
    elif marker_val == "☆": 
        card_class += " active-hoshi"
    elif marker_val == "✖": 
        card_class += " active-keshi"
    
    if marker_val == "未設定" and raw_points > 0: 
        border_left_style = "5px solid #4CAF50"
    else: 
        border_left_style = "1px solid #E0E0E0" if marker_val == "未設定" else "none"
    
    text_color_style = "color: #111111;"
    badge_opacity = "opacity: 1.0;"
    points_style = "color: #FF9800;"
    stars_txt = f" ({stars})" if stars else ""
    
    if marker_val == "✖": 
        text_color_style = "color: #888888;"
        badge_opacity = "opacity: 0.35; filter: grayscale(100%);"
        points_style = "color: #90A4AE;"
        stars_txt = ""
    
    o_val = row_data.get('オッズ', None)
    p_val = row_data.get('人気', None)
    try:
        o_f = float(o_val)
        odds_txt = f"{o_f}倍" if o_f > 0 else "未取得"
    except (ValueError, TypeError):
        odds_txt = "未取得"
    try:
        p_f = float(p_val)
        pop_txt = f"{int(p_f)}人気" if p_f > 0 else "未設定"
    except (ValueError, TypeError):
        pop_txt = "未設定"
    
    jockey_name = row_data.get('騎手', 'ー')
    j_pair = row_data.get('騎手_ペア', '')
    jockey_pair_txt = f"(ペア: {j_pair})" if pd.notnull(j_pair) and j_pair != "" else ""
    
    stable_name = row_data.get('調教師', 'ー')
    t_pair = row_data.get('調教師_ペア', '')
    stable_pair_txt = f"(ペア: {t_pair})" if pd.notnull(t_pair) and t_pair != "" else ""
    
    owner_name = row_data.get('馬主(最新/仮想)', 'ー') if '馬主(最新/仮想)' in row_data.index else 'ー'
    o_pair = row_data.get('馬主(最新/仮想)_ペア', '') if '馬主(最新/仮想)_ペア' in row_data.index else ''
    owner_pair_txt = f"(ペア: {o_pair})" if pd.notnull(o_pair) and o_pair != "" else ""
    
    owner_section_html = ""
    if pd.notnull(owner_name) and owner_name != "ー":
        owner_section_html = f"<div>👑 馬主: <strong style='font-size:14px; color:#111;'>{owner_name}</strong> <span style='color: #4CAF50; font-size: 11px; font-weight:bold; margin-left:4px;'>{owner_pair_txt}</span></div>"

    haichi_elements_html = f"""
    <div style="font-size: 13px; color: #333333; background-color: #FAFAFA; padding: 8px 12px; border-radius: 8px; margin-bottom: 8px; line-height: 1.5; border: 1px solid #ECEFF1; {badge_opacity}">
        <div style="margin-bottom: 2px;">👤 騎手: <strong style="font-size:14px; color:#111;">{jockey_name}</strong> <span style="color:#D500F9; font-size: 11px; font-weight:bold; margin-left:4px;">{jockey_pair_txt}</span></div>
        <div style="margin-bottom: 2px;">🏠 厩舎: <strong style="font-size:14px; color:#111;">{stable_name}</strong> <span style="color:#00B0FF; font-size: 11px; font-weight:bold; margin-left:4px;">{stable_pair_txt}</span></div>
        {owner_section_html}
    </div>"""
    
    perf_score = row_data.get('総合指数', 0.0)
    perf_rank = row_data.get('前走着順')
    perf_rank_txt = f"{int(perf_rank)}着" if (pd.notnull(perf_rank) and not pd.isna(perf_rank)) else "-"
    perf_interval = row_data.get('レース間隔', '-')
    perf_level = row_data.get('レベル点', 0.0)
    perf_jiri = row_data.get('自力点', 0.0)
    perf_bonus = row_data.get('ボーナス減点', 0.0)
    perf_hoso = row_data.get('好走/次走あり', '-')
    perf_diff = row_data.get('前走着差', '-')
    perf_leg = row_data.get('前走脚質', '-')
    perf_kyuyo = row_data.get('長期休養フラグ', '-')
    
    kyuyo_html = ""
    if "🚩" in str(perf_kyuyo): 
        kyuyo_html = f"""<div style="background-color: #E8F5E9; color: #2E7D32; font-size: 11px; font-weight: bold; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #A5D6A7;">{perf_kyuyo}</div>"""
    elif perf_kyuyo != "-": 
        kyuyo_html = f"""<div style="background-color: #F5F5F5; color: #616161; font-size: 11px; padding: 4px; border-radius: 4px; margin-top: 4px; border: 1px solid #E0E0E0;">{perf_kyuyo}</div>"""

    perf_section_html = f"""
    <div style="margin-top: 8px; margin-bottom: 8px; padding: 8px 12px; background-color: #FFFDE7; border: 1px solid #FFF59D; border-radius: 8px; {badge_opacity}">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: bold; font-size: 13px; color: #F57F17; display: flex; align-items: center; gap: 4px;"><span>⚡</span> <span>黄金比指数:</span></span>
            <span style="font-weight: bold; font-size: 14px; color: #E65100;">{perf_score if pd.notnull(perf_score) else 0.0} 点</span>
        </div>
        <div style="font-size: 11.5px; color: #5D4037; display: flex; flex-wrap: wrap; row-gap: 2px; column-gap: 8px;">
            <span>前走: <strong>{perf_rank_txt}</strong> ({perf_diff})</span>
            <span>間隔: <strong>{perf_interval}</strong></span>
            <span>脚質: <strong>{perf_leg}</strong></span>
            <span>相手: <strong>{perf_level if pd.notnull(perf_level) else 0.0}点</strong></span>
            <span>自力: <strong>{perf_jiri if pd.notnull(perf_jiri) else 0.0}点</strong></span>
            <span>加減: <strong>{perf_bonus if pd.notnull(perf_bonus) else 0.0}点</strong></span>
            <div style="width: 100%; border-top: 1px dashed #CCC; margin-top: 4px; padding-top: 4px; font-size: 10.5px;">
                {perf_hoso}
            </div>
        </div>
        {kyuyo_html}
    </div>"""
    
    t4 = format_lap_time(row_data.get('4Fタイム', np.nan))
    l4 = format_lap_time(row_data.get('Lap4', np.nan))
    l3 = format_lap_time(row_data.get('Lap3', np.nan))
    l2 = format_lap_time(row_data.get('Lap2', np.nan))
    l1 = format_lap_time(row_data.get('ラスト1F', np.nan))
    perf_lapeval = row_data.get('ラップ評価', '-')

    hanro_bg = "#ECEFF1"
    hanro_color = "#37474F"
    hanro_border = "#CFD8DC"
    
    if "🌟激アツ" in str(perf_lapeval): 
        hanro_bg = "#FFF3E0"
        hanro_color = "#E65100"
        hanro_border = "#FFE0B2"
    elif "🚨危険" in str(perf_lapeval): 
        hanro_bg = "#FFEBEE"
        hanro_color = "#C62828"
        hanro_border = "#FFCDD2"
        
    if t4 != "-":
        hanro_html = f"""<div style="margin-top: 6px; padding: 8px 12px; background-color: {hanro_bg}; color: {hanro_color}; border: 1px solid {hanro_border}; border-radius: 8px; font-size: 11.5px; {badge_opacity}">
            <div style="font-weight: bold; margin-bottom: 2px;">🏃 坂路: {t4}秒 ({l4}-{l3}-{l2}-{l1})</div>
            <div style="font-size: 11px; font-weight: bold;">評: {perf_lapeval}</div>
        </div>"""
    else:
        hanro_html = f"""<div style="margin-top: 6px; padding: 8px 12px; background-color: #F9F9F9; color: #9E9E9E; border: 1px dashed #E0E0E0; border-radius: 8px; font-size: 11.5px; {badge_opacity}">
            <div style="font-weight: bold; margin-bottom: 2px;">🏃 坂路: データなし</div>
        </div>"""
    
    badges_html_list = []
    checks = [
        ("騎手", row_data.get('騎手判定', 'ー')), 
        ("調教", row_data.get('調教師判定', 'ー')), 
        ("馬主", row_data.get('馬主判定', 'ー') if '馬主判定' in row_data.index else 'ー'), 
        ("サイン", row_data.get('配置サイン', 'ー'))
    ]
    for l_t, dec in checks:
        b_h = make_badge_html(l_t, dec)
        if b_h: badges_html_list.append(b_h)
    
    dec_badges_section = f"""<div style="display: flex; flex-direction: column; gap: 2px; margin-top: 4px; {badge_opacity}">{"".join(badges_html_list)}</div>""" if badges_html_list else ""
    
    if horse_key not in st.session_state['partner_cache']:
        st.session_state['partner_cache'][horse_key] = find_all_pair_partners_detailed(row_data, curr_df)
        
    detailed_partners = st.session_state['partner_cache'][horse_key]
    future_partner_html = ""
    if detailed_partners:
        p_list = [f"""<div style="margin-top: 4px; font-size: 11px; line-height: 1.4; background-color: #FFFFFF; padding: 4px 8px; border-radius: 4px; border-left: 3px solid #0066CC; box-shadow: 0 1px 2px rgba(0,0,0,0.05); color: #333333;">{p}</div>""" for p in detailed_partners]
        future_partner_html = f"""
        <div style="margin-top: 8px; padding: 8px; background-color: #EBF3FC; border: 1px solid #B3D4FF; border-radius: 8px; {badge_opacity}">
            <div style="font-weight: bold; font-size: 12px; color: #0052CC; display: flex; align-items: center; gap: 4px; margin-bottom: 4px;">
                <span style="font-size: 14px;">🔮</span><span>同期ペア情報:</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 2px;">{"".join(p_list)}</div>
        </div>"""
    
    r_num_badge = f"<span style='background-color:#000; color:#FFF; font-size:12px; padding:2px 6px; border-radius:4px; margin-right:4px;'>{r_num}R</span>"
    
    card_html = textwrap.dedent(f"""
    <div class="{card_class}" style="border-left: {border_left_style} !important; padding: 12px; box-shadow: 1px 1px 4px rgba(0,0,0,0.05); font-family: sans-serif; {text_color_style} display: flex; flex-direction: column; justify-content: space-between; overflow: hidden;">
        <div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 4px;">
                    {r_num_badge}
                    <span style="background-color: {waku_bg}; color: {waku_fg}; border: 2px solid {waku_border}; font-size: 14px; font-weight: bold; padding: 2px 6px; border-radius: 50%; box-shadow: 1px 1px 3px rgba(0,0,0,0.2); display: inline-block; min-width: 26px; text-align: center;">{num_val}</span>
                    <span style="font-size: 17px; font-weight: bold; margin-left:2px;">{name_val}</span>
                </div>
                <div style="text-align: right; {text_color_style}">
                    <span style="font-size: 13px; font-weight: bold;">{odds_txt} <span style="font-size:11px; font-weight:normal; color:#666;">({pop_txt})</span></span>
                    <div style="{points_style} font-size: 14px; font-weight: bold; margin-top: 2px;">{raw_points}点{stars_txt}</div>
                </div>
            </div>
            {haichi_elements_html}
            {perf_section_html}
            {hanro_html}
            {dec_badges_section}
        </div>
        {future_partner_html}
    </div>
    """).strip()
    
    with st.container(border=False):
        try: st.html(card_html)
        except AttributeError: st.markdown(card_html, unsafe_allow_html=True)
        
        b_cols1 = st.columns(4)
        with b_cols1[0]:
            if st.button("◎本命", key=f"btn_honmei_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "◎"
                st.rerun()
        with b_cols1[1]:
            if st.button("○対抗", key=f"btn_taikou_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "○"
                st.rerun()
        with b_cols1[2]:
            if st.button("▲単穴", key=f"btn_tanana_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "▲"
                st.rerun()
        with b_cols1[3]:
            if st.button("△連下", key=f"btn_renka_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "△"
                st.rerun()
        
        b_cols2 = st.columns(3)
        with b_cols2[0]:
            if st.button("☆穴馬", key=f"btn_hoshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "☆"
                st.rerun()
        with b_cols2[1]:
            if st.button("✖消す", key=f"btn_keshi_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "✖"
                st.rerun()
        with b_cols2[2]:
            if st.button("⚪戻す", key=f"btn_clear_{horse_key}", use_container_width=True): 
                st.session_state['user_markers'][horse_key] = "未設定"
                st.rerun()

# 🌟 JSを利用した「絶対潰れない＆横スクロール」のレンダリング関数 🌟
def render_horse_cards_carousel(h_list, selected_venue, curr_df, cards_per_row=3, block_key=None):
    """3枚グリッド＋ページネーション（コールバック方式・rerun不使用）"""
    if not h_list:
        return

    if isinstance(h_list, pd.DataFrame):
        h_list = h_list.to_dict('records')

    total = len(h_list)
    if total == 0:
        return

    # block_key が未指定の場合は馬名ハッシュで固定
    if block_key is None:
        try:
            names = tuple(str(r.get('馬名', i)) if hasattr(r, 'get') else str(i) for i, r in enumerate(h_list[:5]))
            block_key = "cp_" + str(abs(hash(names)))[:10]
        except Exception:
            block_key = "cp_default"

    page_key = f"carousel_page_{block_key}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    # 1ページ = 3列 × 6行 = 最大18頭（1レース分）
    page_size   = cards_per_row * 6
    total_pages = max(1, -(-total // page_size))

    # コールバック関数（rerun不要・ボタン押下時に即座にページ番号を更新）
    def go_prev():
        st.session_state[page_key] = max(0, st.session_state[page_key] - 1)

    def go_next():
        st.session_state[page_key] = min(total_pages - 1, st.session_state[page_key] + 1)

    current_page = min(st.session_state[page_key], total_pages - 1)
    start_idx    = current_page * page_size
    end_idx      = min(start_idx + page_size, total)
    page_items   = h_list[start_idx:end_idx]

    # ページナビ（複数ページある場合のみ）
    if total_pages > 1:
        nav_cols = st.columns([1, 4, 1])
        with nav_cols[0]:
            st.button("◀ 前",
                      key=f"prev_{block_key}",
                      disabled=(current_page == 0),
                      on_click=go_prev,
                      use_container_width=True)
        with nav_cols[1]:
            st.markdown(
                f"<div style='text-align:center;color:#666;font-size:13px;padding-top:6px;'>"
                f"{start_idx+1}〜{end_idx} / {total}頭　"
                f"({current_page+1}/{total_pages}ページ)</div>",
                unsafe_allow_html=True
            )
        with nav_cols[2]:
            st.button("次 ▶",
                      key=f"next_{block_key}",
                      disabled=(current_page >= total_pages - 1),
                      on_click=go_next,
                      use_container_width=True)

    # cards_per_row 枚ずつ行に並べる
    for row_start in range(0, len(page_items), cards_per_row):
        row_items = page_items[row_start:row_start + cards_per_row]
        cols = st.columns(len(row_items))
        for col, row_data in zip(cols, row_items):
            with col:
                render_single_horse_card(row_data, selected_venue, curr_df)
        st.markdown("<div style='margin-bottom:12px;'></div>", unsafe_allow_html=True)



# ============================================================
# GitHub取得（フォルダ別・最新ファイル自動取得）
# ============================================================
def _github_latest_file(folder: str) -> tuple:
    """指定フォルダ内の最新CSVを取得して (ファイル名, bytes) を返す。"""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{folder}?ref={GITHUB_BRANCH}"
    try:
        r = requests.get(api_url, timeout=10, headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            logger.warning(f"GitHub API失敗 ({r.status_code}): {folder}")
            return None, None
        files = [f for f in r.json() if isinstance(f, dict)
                 and f.get("type") == "file" and f.get("name","").lower().endswith(".csv")]
        if not files:
            return None, None
        files.sort(key=lambda x: x["name"], reverse=True)
        latest = files[0]
        res = requests.get(latest["download_url"], timeout=15)
        if res.status_code == 200:
            logger.info(f"GitHub取得成功: {folder}/{latest['name']}")
            return latest["name"], res.content
        return None, None
    except Exception as e:
        logger.warning(f"GitHub取得エラー ({folder}): {e}")
        return None, None

@st.cache_data(ttl=3600)
def load_github_data():
    """各フォルダの最新CSVを自動取得して bytes を返す。"""
    curr_name,  curr_bytes  = _github_latest_file(GITHUB_FOLDER_TODAY)
    prev_name,  prev_bytes  = _github_latest_file(GITHUB_FOLDER_PREV)
    hanro_name, hanro_bytes = _github_latest_file(GITHUB_FOLDER_TRAIN)
    hist_name,  hist_bytes  = _github_latest_file(GITHUB_FOLDER_DATA)
    return (curr_bytes, prev_bytes, hanro_bytes, hist_bytes,
            curr_name or "", prev_name or "", hanro_name or "", hist_name or "")

@st.cache_resource
def get_master_history_data():
    for fp in MASTER_CSV_CANDIDATES:
        if os.path.exists(fp):
            try:
                df = _read_csv_with_encoding(fp)
                df["date"] = df["日付"].apply(parse_date)
                if "レースID(新)" in df.columns:
                    df["race_id"] = df["レースID(新)"].astype(str).str.strip().str[:-2]
                else:
                    df["race_id"] = df["日付"].astype(str)+df["場所"].astype(str)+df["Ｒ"].astype(str)
                df["rank"] = df["着順"].apply(normalize_rank)
                df["馬名"] = df["馬名"].apply(clean_horse_name)
                return df.dropna(subset=["date"]).sort_values(["馬名","date"]).reset_index(drop=True)
            except Exception as e:
                logger.warning(f"history読込失敗: {e}")
    return None

# ============================================================
# スマホ UI ヘルパー
# ============================================================
MARK_LABELS = {"◎":"honmei","○":"taikou","▲":"tanana","△":"renka","☆":"hoshi","✖":"keshi"}
MARK_COLORS = {"◎":"#FF5722","○":"#2196F3","▲":"#4CAF50","△":"#FFC107","☆":"#9C27B0","✖":"#757575"}

def get_mark(venue, r_num, uma_num):
    return st.session_state["user_markers"].get(f"{venue}_{r_num}_{uma_num}", "")

def set_mark(venue, r_num, uma_num, mark):
    key = f"{venue}_{r_num}_{uma_num}"
    if st.session_state["user_markers"].get(key) == mark:
        st.session_state["user_markers"].pop(key, None)
    else:
        st.session_state["user_markers"][key] = mark

def render_mobile_card(row, venue, curr_df):
    """1頭分のスマホカード（タップで詳細展開）"""
    uma_num  = int(row.get("馬番", 0))
    r_num    = int(row.get("Ｒ", 0))
    name     = escape_html(str(row.get("馬名", "不明")))
    jockey   = str(row.get("騎手", "不明"))
    stable   = str(row.get("調教師", "不明"))
    owner    = str(row.get("馬主(最新/仮想)", ""))
    mark     = get_mark(venue, r_num, uma_num)
    css_cls  = MARK_LABELS.get(mark, "")

    # オッズ・人気
    try:
        o_f = float(row.get("オッズ", 0))
        odds_txt = f"{o_f}倍" if o_f > 0 else "未取得"
    except Exception:
        odds_txt = "未取得"
    try:
        p_f = float(row.get("人気", 0))
        pop_txt = f"{int(p_f)}人気" if p_f > 0 else ""
    except Exception:
        pop_txt = ""

    # 能力指数
    try:
        score_txt = f"指数:{float(row.get('総合指数', 0)):.1f}"
    except Exception:
        score_txt = ""

    # 前走情報
    try:
        prev_rank = int(row.get("前走着順", 0))
        prev_rank_txt = f"前走{prev_rank}着" if prev_rank > 0 else ""
    except Exception:
        prev_rank_txt = ""

    interval = str(row.get("レース間隔", ""))
    interval_txt = f"中{interval}週" if interval and interval not in ("", "nan", "-") else ""

    # ペア情報
    j_pair = str(row.get("騎手_ペア", ""))
    j_pair_txt = f"騎手ペア: {j_pair}" if j_pair and j_pair not in ("", "nan") else ""
    t_pair = str(row.get("調教師_ペア", ""))
    t_pair_txt = f"調教師ペア: {t_pair}" if t_pair and t_pair not in ("", "nan") else ""

    # バッジ
    badges = ""
    if row.get("騎手_青塗"):     badges += '<span class="m-badge badge-ao">青塗騎手</span>'
    if row.get("騎手_黄塗"):     badges += '<span class="m-badge badge-ki">黄塗騎手</span>'
    if row.get("調教師_青塗"):   badges += '<span class="m-badge badge-ao">青塗調教師</span>'
    if row.get("調教師_黄塗"):   badges += '<span class="m-badge badge-ki">黄塗調教師</span>'
    if row.get("お詫び好走候補"): badges += '<span class="m-badge badge-owabi">お詫び</span>'

    # 枠色
    waku_info  = get_waku_info(uma_num, int(row.get("頭数", 16)))
    waku_color = waku_info[1] if waku_info else "#ccc"
    mark_color = MARK_COLORS.get(mark, "#ccc")
    mark_disp  = f"<span style='font-size:22px;font-weight:900;color:{mark_color};margin-left:6px;'>{mark}</span>" if mark else ""

    card_html = f"""
<div class="m-card {css_cls}">
  <div style="display:flex;align-items:center;gap:8px;">
    <div style="width:36px;height:36px;border-radius:50%;background:{waku_color};
         display:flex;align-items:center;justify-content:center;
         font-weight:900;font-size:16px;color:#fff;flex-shrink:0;">{uma_num}</div>
    <span class="m-horse-name">{name}</span>
    <span class="m-odds">{odds_txt}</span>
    {mark_disp}
  </div>
  <div class="m-jockey">🏇 {jockey}　<span style="color:#888;font-size:13px;">{pop_txt}</span></div>
  <div style="font-size:13px;color:#666;margin-top:2px;">{score_txt}　{prev_rank_txt}　{interval_txt}</div>
  <div style="margin-top:4px;">{badges}</div>
</div>"""
    st.markdown(card_html, unsafe_allow_html=True)

    # 詳細展開（st.expander）- PC版と同じ情報を表示
    with st.expander("📋 詳細を見る", expanded=False):

        # ---- 騎手・調教師・馬主 ----
        haichi_html = f"""
<div style="font-size:14px;background:#FAFAFA;padding:10px 14px;border-radius:8px;
     margin-bottom:8px;line-height:1.8;border:1px solid #ECEFF1;">
  <div>👤 騎手: <strong style="color:#111;">{jockey}</strong>
    <span style="color:#D500F9;font-size:12px;font-weight:bold;margin-left:6px;">{j_pair_txt}</span></div>
  <div>🏠 調教師: <strong style="color:#111;">{stable}</strong>
    <span style="color:#00B0FF;font-size:12px;font-weight:bold;margin-left:6px;">{t_pair_txt}</span></div>
  {'<div>👑 馬主: <strong style="color:#111;">' + owner + '</strong></div>' if owner and owner not in ("不明","nan","") else ""}
</div>"""
        st.markdown(haichi_html, unsafe_allow_html=True)

        # ---- 黄金比指数 ----
        try:
            perf_score  = float(row.get("総合指数", 0) or 0)
            perf_level  = float(row.get("レベル点", 0) or 0)
            perf_jiri   = float(row.get("自力点", 0) or 0)
            perf_bonus  = float(row.get("ボーナス減点", 0) or 0)
        except Exception:
            perf_score = perf_level = perf_jiri = perf_bonus = 0.0

        prev_rank  = row.get("前走着順", "-")
        try: prev_rank_txt = f"{int(float(prev_rank))}着"
        except Exception: prev_rank_txt = "-"
        prev_diff  = row.get("前走着差", "-")
        interval   = row.get("レース間隔", "-")
        prev_leg   = row.get("前走脚質", "-")
        hoso       = row.get("好走/次走あり", "-")
        kyuyo      = str(row.get("長期休養フラグ", "-"))

        kyuyo_html = ""
        if "🚩" in kyuyo:
            kyuyo_html = f"<div style='background:#E8F5E9;color:#2E7D32;font-size:12px;font-weight:bold;padding:4px;border-radius:4px;margin-top:4px;border:1px solid #A5D6A7;'>{kyuyo}</div>"
        elif kyuyo not in ("-","nan","None",""):
            kyuyo_html = f"<div style='background:#F5F5F5;color:#616161;font-size:12px;padding:4px;border-radius:4px;margin-top:4px;border:1px solid #E0E0E0;'>{kyuyo}</div>"

        perf_html = f"""
<div style="padding:10px 14px;background:#FFFDE7;border:1px solid #FFF59D;border-radius:8px;margin-bottom:8px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
    <span style="font-weight:bold;font-size:14px;color:#F57F17;">⚡ 黄金比指数</span>
    <span style="font-weight:bold;font-size:16px;color:#E65100;">{perf_score:.1f} 点</span>
  </div>
  <div style="font-size:13px;color:#5D4037;display:flex;flex-wrap:wrap;gap:8px;">
    <span>前走: <strong>{prev_rank_txt}</strong> ({prev_diff})</span>
    <span>間隔: <strong>{interval}</strong></span>
    <span>脚質: <strong>{prev_leg}</strong></span>
    <span>相手: <strong>{perf_level:.1f}点</strong></span>
    <span>自力: <strong>{perf_jiri:.1f}点</strong></span>
    <span>加減: <strong>{perf_bonus:.1f}点</strong></span>
  </div>
  <div style="font-size:12px;color:#795548;border-top:1px dashed #CCC;margin-top:6px;padding-top:6px;">{hoso}</div>
  {kyuyo_html}
</div>"""
        st.markdown(perf_html, unsafe_allow_html=True)

        # ---- 坂路調教 ----
        t4 = format_lap_time(row.get("4Fタイム", np.nan))
        l4 = format_lap_time(row.get("Lap4", np.nan))
        l3 = format_lap_time(row.get("Lap3", np.nan))
        l2 = format_lap_time(row.get("Lap2", np.nan))
        l1 = format_lap_time(row.get("ラスト1F", np.nan))
        lap_eval = str(row.get("ラップ評価", "-"))

        if "🌟激アツ" in lap_eval:
            hbg, hcol, hbd = "#FFF3E0", "#E65100", "#FFE0B2"
        elif "🚨危険" in lap_eval:
            hbg, hcol, hbd = "#FFEBEE", "#C62828", "#FFCDD2"
        else:
            hbg, hcol, hbd = "#ECEFF1", "#37474F", "#CFD8DC"

        if t4 != "-":
            hanro_html = f"""
<div style="padding:10px 14px;background:{hbg};color:{hcol};border:1px solid {hbd};border-radius:8px;margin-bottom:8px;font-size:13px;">
  <div style="font-weight:bold;margin-bottom:2px;">🏃 坂路: {t4}秒 ({l4}-{l3}-{l2}-{l1})</div>
  <div style="font-size:12px;font-weight:bold;">評価: {lap_eval}</div>
</div>"""
        else:
            hanro_html = """
<div style="padding:10px 14px;background:#F9F9F9;color:#9E9E9E;border:1px dashed #E0E0E0;border-radius:8px;margin-bottom:8px;font-size:13px;">
  <div style="font-weight:bold;">🏃 坂路: データなし</div>
</div>"""
        st.markdown(hanro_html, unsafe_allow_html=True)

        # ---- 判定バッジ ----
        checks = [
            ("騎手",  str(row.get("騎手判定",  "ー"))),
            ("調教",  str(row.get("調教師判定", "ー"))),
            ("馬主",  str(row.get("馬主判定",   "ー"))),
            ("サイン",str(row.get("配置サイン", "ー"))),
        ]
        badge_parts = [make_badge_html(lt, dec) for lt, dec in checks]
        badge_parts = [b for b in badge_parts if b]
        if badge_parts:
            st.markdown(
                f"<div style='display:flex;flex-direction:column;gap:4px;margin-bottom:8px;'>{''.join(badge_parts)}</div>",
                unsafe_allow_html=True
            )

        # ---- 同期ペア情報 ----
        horse_key = f"{venue}_{r_num}_{uma_num}"
        if horse_key not in st.session_state["partner_cache"]:
            st.session_state["partner_cache"][horse_key] = find_all_pair_partners_detailed(row, curr_df)
        detailed_partners = st.session_state["partner_cache"][horse_key]
        if detailed_partners:
            p_items = "".join([
                f"<div style='font-size:12px;line-height:1.5;background:#FFF;padding:4px 8px;border-radius:4px;border-left:3px solid #0066CC;margin-top:4px;'>{p}</div>"
                for p in detailed_partners
            ])
            st.markdown(f"""
<div style="padding:10px;background:#EBF3FC;border:1px solid #B3D4FF;border-radius:8px;margin-bottom:8px;">
  <div style="font-weight:bold;font-size:13px;color:#0052CC;margin-bottom:4px;">🔮 同期ペア情報</div>
  {p_items}
</div>""", unsafe_allow_html=True)

    # 印ボタン（横一列）
    btn_key_base = f"mbtn_{venue}_{r_num}_{uma_num}"
    cols = st.columns(7)
    for ci, (mk, _) in enumerate([("◎",""),("○",""),("▲",""),("△",""),("☆",""),("✖",""),("－","")]):
        with cols[ci]:
            label  = mk if mk != "－" else "消"
            active = (mark == mk)
            if st.button(label, key=f"{btn_key_base}_{mk}",
                         use_container_width=True,
                         type="primary" if active else "secondary"):
                if mk == "－":
                    st.session_state["user_markers"].pop(f"{venue}_{r_num}_{uma_num}", None)
                else:
                    set_mark(venue, r_num, uma_num, mk)
                st.rerun()

# ============================================================
# メイン処理
# ============================================================
st.markdown("## 🏇 配置馬券 スマホ版")

# --- データ取得 ---
with st.expander("📡 データ取得 / アップロード", expanded=False):
    data_mode = st.radio("取得方法", ["📡 GitHub自動取得", "📁 手動アップロード"], horizontal=True)

    if st.button("🔄 最新データを取得", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    github_curr_bytes = github_prev_bytes = github_hanro_bytes = github_hist_bytes = None
    curr_files = prev_files = uploaded_hanro = None
    history_df = None

    if data_mode == "📡 GitHub自動取得":
        github_curr_bytes, github_prev_bytes, github_hanro_bytes, github_hist_bytes,             _curr_name, _prev_name, _hanro_name, _hist_name = load_github_data()

        if github_curr_bytes:
            st.success(f"✅ 出馬表：{_curr_name}")
        else:
            st.warning(f"⚠️ 出馬表：today/ にCSVが見つかりません")
        if github_prev_bytes:
            st.success(f"✅ 前日結果：{_prev_name}")
        else:
            st.info("ℹ️ 前日結果：prev/ なし（任意）")
        if github_hanro_bytes:
            st.success(f"✅ 坂路：{_hanro_name}")
        else:
            st.info("ℹ️ 坂路：train/ なし（任意）")
        if github_hist_bytes:
            st.success(f"✅ 過去DB：{_hist_name}")
        else:
            st.info("ℹ️ 過去DB：data/ なし（任意）")
    else:
        _curr_name = _prev_name = _hanro_name = ""
        curr_upload  = st.file_uploader("当日出馬表CSV", type=["csv"], accept_multiple_files=True)
        prev_upload  = st.file_uploader("前日結果CSV",   type=["csv"], accept_multiple_files=True)
        hanro_upload = st.file_uploader("坂路CSV（任意）", type=["csv"])
        if curr_upload:
            curr_files = curr_upload
        if prev_upload:
            prev_files = prev_upload
        if hanro_upload:
            uploaded_hanro = hanro_upload

    # GitHub bytes → file-like に変換（ファイル名も正しくセット）
    if github_curr_bytes:
        _f = io.BytesIO(github_curr_bytes); _f.name = _curr_name or "today.csv"
        curr_files = [_f]
    if github_prev_bytes:
        _f = io.BytesIO(github_prev_bytes); _f.name = _prev_name or "prev.csv"
        prev_files = [_f]
    if github_hanro_bytes:
        _f = io.BytesIO(github_hanro_bytes); _f.name = _hanro_name or "train.csv"
        uploaded_hanro = _f
    if github_hist_bytes and history_df is None:
        try:
            _hist_df = get_manual_history_data(github_hist_bytes)
            if _hist_df is not None:
                history_df = _hist_df
        except Exception as _e:
            logger.warning(f"過去DB自動ロード失敗: {_e}")

# --- history DB ---
if history_df is None:
    history_df = get_master_history_data()
# それでもNoneならGitHub data/から取得
if history_df is None:
    _gh_hist_name, _gh_hist_bytes = _github_latest_file(GITHUB_FOLDER_DATA)
    if _gh_hist_bytes:
        history_df = get_manual_history_data(_gh_hist_bytes)
global_target_datetime = pd.to_datetime(date.today())

# --- CSV処理 ---
curr_df = st.session_state.get("fully_processed_df", pd.DataFrame())
combo_key = (",".join([f.name for f in curr_files]) if curr_files else "") + "_mobile"

if curr_files and st.session_state.get("last_processed_key") != combo_key:
    with st.spinner("データを解析中..."):
        dfs = []
        for f in curr_files:
            f.seek(0)
            for enc in ["utf-8","shift_jis","cp932"]:
                try:
                    dfs.append(pd.read_csv(f, encoding=enc)); break
                except Exception:
                    f.seek(0)
        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            ODDS_COL_ALIASES = ["単勝オッズ","単勝","オッズ","単オッズ","単勝(元値)","予想オッズ","想定オッズ"]
            POP_COL_ALIASES  = ["単勝人気","人気","確定人気","人気順"]
            rename_cols = {}
            for col in df.columns:
                if col.strip() in ODDS_COL_ALIASES and col.strip() != "オッズ":
                    rename_cols[col] = "オッズ"
                elif col.strip() in POP_COL_ALIASES and col.strip() != "人気":
                    rename_cols[col] = "人気"
                elif col.strip() == "馬主":
                    rename_cols[col] = "馬主(最新/仮想)"
            if rename_cols: df = df.rename(columns=rename_cols)
            if "オッズ" not in df.columns: df["オッズ"] = np.nan
            if "人気" not in df.columns:  df["人気"]  = np.nan
            df["馬名"] = df["馬名"].apply(clean_horse_name)
            df = preprocess_and_calculate_haichi(df)

            if prev_files:
                prev_dfs = []
                for f in prev_files:
                    f.seek(0)
                    for enc in ["utf-8","shift_jis","cp932"]:
                        try:
                            prev_dfs.append(pd.read_csv(f, encoding=enc)); break
                        except Exception:
                            f.seek(0)
                if prev_dfs:
                    prev_df = pd.concat(prev_dfs, ignore_index=True)
                    prev_df["馬名"] = prev_df["馬名"].apply(clean_horse_name)
                    df = judge_yellow_and_pairs(df, "騎手")
                    df, _ = judge_blue_coating(df, "騎手")
                    df = judge_yellow_and_pairs(df, "調教師")
                    df, _ = judge_blue_coating(df, "調教師")
                    df = judge_yellow_and_pairs(df, "馬主(最新/仮想)")
                    df, _ = judge_blue_coating(df, "馬主(最新/仮想)")

            # ---- 坂路CSVのマージ（PC版と同一ロジック） ----
            # 列構造: 場所,年月日,曜日,馬番/仮番(...),馬名,性別,年齢,調教師,Time1,...,Lap4,Lap3,Lap2,Lap1
            # ヘッダーあり・cp932エンコード
            hanro_clean_df = None
            if uploaded_hanro is not None:
                try:
                    df_hanro = None
                    for _enc in ["utf-8", "shift_jis", "cp932"]:
                        try:
                            if hasattr(uploaded_hanro, "seek"):
                                uploaded_hanro.seek(0)
                            df_hanro = pd.read_csv(uploaded_hanro, encoding=_enc)
                            break
                        except Exception:
                            continue
                    if df_hanro is None:
                        raise ValueError("坂路CSV: エンコード検出失敗")

                    df_hanro.columns = df_hanro.columns.str.strip()
                    df_hanro["馬名"] = df_hanro["馬名"].apply(clean_horse_name)

                    if all(col in df_hanro.columns for col in ["Time1", "Lap4", "Lap3", "Lap2", "Lap1"]):
                        def classify_lap_m(row):
                            l2 = row["Lap2"]; l1 = row["Lap1"]
                            if pd.isna(l2) or pd.isna(l1): return "-"
                            if l2 > l1:
                                accel_diff = round(l2 - l1, 1)
                                if 12.0 <= l1 <= 12.9:
                                    if l2 >= 13.0: return "1 終いのみ12秒台の加速"
                                    elif 12.0 <= l2 <= 12.9: return "3 終い2F12秒台まとめの加速"
                                elif l1 <= 11.9:
                                    if accel_diff >= 0.5: return "🌟激アツ: 5 終い11秒台の急加速(0.5秒以上)"
                                    else: return "5 終い11秒台の加速"
                                return "-"
                            else:
                                decel_diff = round(l1 - l2, 1)
                                if 12.0 <= l2 <= 12.9:
                                    if l1 >= 13.0: return "🚨危険(地雷): 2 2Fのみ12秒台の減速"
                                    elif 12.0 <= l1 <= 12.9: return "4 終い2F12秒台まとめの減速"
                                elif l2 <= 11.9:
                                    if l1 >= 12.0:
                                        if decel_diff <= 0.4: return "🌟激アツ: 6 2Fのみ11秒台の微減速(0.4秒以内)"
                                        else: return "6 2Fのみ11秒台の減速"
                                    else: return "終い2F11秒台まとめの減速"
                                return "-"

                        df_hanro["ラップ評価"] = df_hanro.apply(classify_lap_m, axis=1)
                        df_hanro["Time1"] = pd.to_numeric(df_hanro["Time1"], errors="coerce")
                        df_hanro = df_hanro.sort_values(by=["馬名", "Time1"], ascending=True)
                        hanro_clean_df = df_hanro.drop_duplicates(subset=["馬名"], keep="first").copy()
                        hanro_clean_df = hanro_clean_df.rename(columns={"Time1": "4Fタイム", "Lap1": "ラスト1F"})
                except Exception as e:
                    logger.warning(f"坂路CSVマージ失敗: {e}")

            if hanro_clean_df is not None:
                df = pd.merge(df, hanro_clean_df[["馬名", "4Fタイム", "Lap4", "Lap3", "Lap2", "ラスト1F", "ラップ評価"]], on="馬名", how="left")
                df["ラップ評価"] = df["ラップ評価"].fillna("-")
            else:
                for col in ["4Fタイム", "Lap4", "Lap3", "Lap2", "ラスト1F"]:
                    df[col] = np.nan
                df["ラップ評価"] = "-"

            if history_df is not None:
                df = apply_performance_levels(df, history_df, global_target_datetime)

            def sf(x):
                try:
                    import unicodedata as _ud
                    s = _ud.normalize("NFKC", str(x)).replace(",","").replace("倍","").strip()
                    return float(s) if s not in ("","nan","None","---","ー") else 0.0
                except Exception:
                    return 0.0
            df["オッズ"] = df["オッズ"].apply(lambda x: sf(x) if sf(x) > 0 else float("nan"))

            st.session_state["fully_processed_df"] = df
            st.session_state["last_processed_key"]  = combo_key
            curr_df = df

# --- 場所選択 ---
if curr_df is None or curr_df.empty:
    st.info("📂 上の「データ取得」からCSVを読み込んでください")
    st.stop()

venue_list = sorted(curr_df["場所"].unique().tolist())
st.markdown("### 🏟 場所選択")
vcols = st.columns(min(len(venue_list), 4))
for i, v in enumerate(venue_list):
    with vcols[i % 4]:
        selected = st.session_state["m_selected_venue"] == v
        if st.button(v, key=f"mv_{v}", use_container_width=True,
                     type="primary" if selected else "secondary"):
            st.session_state["m_selected_venue"] = v
            st.session_state["m_selected_race"]  = None
            st.rerun()

selected_venue = st.session_state.get("m_selected_venue")
if not selected_venue or selected_venue not in venue_list:
    selected_venue = venue_list[0]
    st.session_state["m_selected_venue"] = selected_venue

# --- レース選択（大ボタングリッド）---
race_list = sorted(curr_df[curr_df["場所"] == selected_venue]["Ｒ"].unique().tolist())
st.markdown(f"### 🔢 {selected_venue} レース選択")
rcols = st.columns(4)
for i, r in enumerate(race_list):
    with rcols[i % 4]:
        sel_r = st.session_state.get("m_selected_race")
        if st.button(f"{r}R", key=f"mr_{selected_venue}_{r}", use_container_width=True,
                     type="primary" if sel_r == r else "secondary"):
            st.session_state["m_selected_race"] = r
            st.rerun()

selected_race = st.session_state.get("m_selected_race")
if selected_race not in race_list:
    selected_race = race_list[0]
    st.session_state["m_selected_race"] = selected_race

# --- 馬カード表示 ---
st.markdown(f"---\n### 🐴 {selected_venue} {selected_race}R")
race_df = curr_df[
    (curr_df["場所"] == selected_venue) &
    (curr_df["Ｒ"] == selected_race)
].sort_values("馬番")

# 印まとめ表示
marks = {}
for k, v in st.session_state["user_markers"].items():
    if k.startswith(f"{selected_venue}_{selected_race}_"):
        marks[v] = marks.get(v, []) + [int(k.split("_")[-1])]
if marks:
    parts = []
    for mk in ["◎","○","▲","△","☆","✖"]:
        if mk in marks:
            nums = ",".join(map(str, sorted(marks[mk])))
            parts.append(f"<span style=\'color:{MARK_COLORS[mk]};font-weight:bold;\'>{mk}{nums}</span>")
    st.markdown(" ".join(parts), unsafe_allow_html=True)

for row in race_df.to_dict('records'):
    render_mobile_card(row, selected_venue, curr_df)

# --- 下部固定ナビ ---
st.markdown("""
<div class="bottom-nav">
  <a href="#"><span>🏠</span>ホーム</a>
  <a href="#"><span>📋</span>レース</a>
  <a href="#"><span>🎯</span>印まとめ</a>
  <a href="#"><span>📊</span>集計</a>
</div>
""", unsafe_allow_html=True)
